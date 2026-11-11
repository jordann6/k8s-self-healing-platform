"""Q1 queue worker: a RabbitMQ consumer KEDA scales on queue depth.

The capability this proves is queue-driven scaling with no lost work. The
hardened profile runs this worker behind a KEDA ScaledObject with
minReplicaCount 0: idle at zero replicas, scaled up by the backlog on the work
queue, scaled back to zero when it drains. The baseline runs a fixed number of
replicas with no autoscaler, so an idle queue still burns the replicas and a
burst has only the fixed capacity.

No lost work is the honesty core, and it is in the ack ordering, not in KEDA:

  1. take one message (manual ack, prefetch 1, so a worker holds at most one
     in flight and a kill loses at most one),
  2. do the unit of work,
  3. publish the message id to the done queue with publisher confirms, and
     only when that is CONFIRMED,
  4. ack the work message.

If the worker dies anywhere before step 4 (a kill mid-work, a broker error on
the done publish), the work message was never acked, so RabbitMQ requeues it
and another worker does it again. The unit of work is therefore at-least-once:
the done queue may carry an id more than once, and the runner counts DISTINCT
ids, so a redelivery never looks like lost work and a drop never hides as one.

A message that cannot be parsed is poison: it is acked away to a dead path and
counted, never requeued forever (that would wedge the queue and, with it, the
only KEDA scale signal). The connection is retried a bounded number of times
and then the process exits non-zero: a worker that cannot reach its broker
crashes visibly rather than sitting Ready with nothing to do, so "no data" can
never read as a healthy idle.

pika is imported only inside connect(), so the pure logic below (parsing, the
ack decision, the work-then-confirm ordering) is unit tested with no broker and
no pika installed. Run: make test
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time

WORK_QUEUE = os.environ.get("WORK_QUEUE", "work")
DONE_QUEUE = os.environ.get("DONE_QUEUE", "done")
DEAD_QUEUE = os.environ.get("DEAD_QUEUE", "dead")
PREFETCH = int(os.environ.get("PREFETCH", "1"))
MAX_WORK_MS = int(os.environ.get("MAX_WORK_MS", "60000"))
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", "4096"))
CONNECT_ATTEMPTS = int(os.environ.get("CONNECT_ATTEMPTS", "30"))
CONNECT_BACKOFF_S = float(os.environ.get("CONNECT_BACKOFF_SECONDS", "2"))
CONFIRM_TIMEOUT_S = float(os.environ.get("CONFIRM_TIMEOUT_SECONDS", "10"))

# Message ids are the runner's own: a short run tag plus an index. Anything else
# is a malformed message, not a unit of work.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] qworker: {msg}", flush=True)


class BadMessage(Exception):
    """The body is not a unit of work. Acked to the dead path, never requeued."""


class PublishFailed(Exception):
    """The done publish was not confirmed. The work message must NOT be acked,
    so RabbitMQ requeues it: at-least-once, no lost work."""


# --------------------------------------------------------------------------- #
# pure logic (unit tested without a broker)
# --------------------------------------------------------------------------- #


def parse_message(body: bytes) -> dict:
    """Validate and decode a work message. Raises BadMessage on anything the
    worker must not treat as work (oversized, not JSON, bad id, bad work_ms)."""
    if not isinstance(body, (bytes, bytearray)):
        raise BadMessage("body is not bytes")
    if len(body) > MAX_BODY:
        raise BadMessage(f"body too large ({len(body)} > {MAX_BODY} bytes)")
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise BadMessage(f"not JSON: {e}") from None
    if not isinstance(obj, dict):
        raise BadMessage("body is not a JSON object")
    mid = obj.get("id")
    if not isinstance(mid, str) or not ID_RE.match(mid):
        raise BadMessage(f"bad or missing id {mid!r}")
    work_ms = obj.get("work_ms", 0)
    if isinstance(work_ms, bool) or not isinstance(work_ms, int) or not 0 <= work_ms <= MAX_WORK_MS:
        raise BadMessage(f"bad work_ms {work_ms!r} (want int in [0, {MAX_WORK_MS}])")
    return {"id": mid, "work_ms": work_ms}


def do_unit(msg: dict, publish_done, sleep=time.sleep) -> None:
    """Do the unit of work, THEN publish the id to the done queue. Returns only
    after publish_done has confirmed; otherwise it raises and the caller does
    not ack. Work before the publish, so a kill mid-work never reaches the done
    queue and the message requeues (no lost work)."""
    sleep(msg["work_ms"] / 1000.0)
    publish_done(msg["id"])


def decide(body: bytes, do, sleep=time.sleep) -> str:
    """Handle one delivery and return the action the caller must take on it:

      "ack"     the unit completed and the id is confirmed on the done queue.
      "dead"    poison: parsed as not-work; ack it away to the dead path.
      "requeue" the unit did not complete (publish not confirmed); do NOT ack,
                so the broker requeues it.

    `do` runs the unit for a parsed message and raises PublishFailed if the
    done publish is not confirmed. Pure: no pika, no network.
    """
    try:
        msg = parse_message(body)
    except BadMessage as e:
        log(f"poison message routed to {DEAD_QUEUE!r}: {e}")
        return "dead"
    try:
        do(msg, sleep=sleep)
    except PublishFailed as e:
        log(f"done publish not confirmed for {msg['id']!r}, leaving it unacked to requeue: {e}")
        return "requeue"
    return "ack"


class Stopper:
    """SIGTERM asks the consumer to stop taking new messages. An in-flight unit
    finishes and acks (clean drain); if the kill is hard (SIGKILL after the
    grace period) mid-unit, the message was never acked and requeues anyway."""

    def __init__(self) -> None:
        self.stop = threading.Event()

    def install(self) -> None:
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, *_a) -> None:
        log("SIGTERM received, stopping consume after the in-flight unit")
        self.stop.set()


# --------------------------------------------------------------------------- #
# broker wiring (pika imported here only)
# --------------------------------------------------------------------------- #


def _params():
    import pika  # deferred: the pure logic above imports without pika present

    url = os.environ.get("RABBITMQ_URL")
    if url:
        return pika.URLParameters(url)
    host = os.environ.get("RABBITMQ_HOST", "rabbitmq.selfheal.svc.cluster.local")
    port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    user = os.environ.get("RABBITMQ_USER", "guest")
    pw = os.environ.get("RABBITMQ_PASS", "guest")
    vhost = os.environ.get("RABBITMQ_VHOST", "/")
    creds = pika.PlainCredentials(user, pw)
    return pika.ConnectionParameters(host=host, port=port, virtual_host=vhost, credentials=creds,
                                     heartbeat=30, blocked_connection_timeout=30)


def connect():
    """Open a connection, retrying a bounded number of times. Raises after the
    last attempt so the pod crashloops instead of idling Ready with no broker."""
    import pika

    last = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            conn = pika.BlockingConnection(_params())
            log(f"connected on attempt {attempt}")
            return conn
        except Exception as e:  # noqa: BLE001 - any broker error is a retry
            last = e
            log(f"connect attempt {attempt}/{CONNECT_ATTEMPTS} failed: {str(e)[:200]}")
            time.sleep(CONNECT_BACKOFF_S)
    raise SystemExit(f"could not reach RabbitMQ after {CONNECT_ATTEMPTS} attempts: {last}")


def _declare(channel) -> None:
    for q in (WORK_QUEUE, DONE_QUEUE, DEAD_QUEUE):
        channel.queue_declare(queue=q, durable=True)


def build_messages(tag: str, n: int, work_ms: int) -> list[bytes]:
    """The backlog the runner enqueues: n work messages with stable, distinct
    ids so the runner can prove every one completed (no lost work). Pure."""
    return [json.dumps({"id": f"{tag}-{i:05d}", "work_ms": work_ms}).encode() for i in range(n)]


def produce() -> None:
    """Publish a backlog, then exit. Driven by the runner as a one-shot Job so
    run.py never needs an AMQP client. Persistent, with confirms: a message the
    broker did not confirm is a failed Job, never a silent short backlog."""
    import pika

    tag = os.environ["Q1_TAG"]
    n = int(os.environ["Q1_N"])
    work_ms = int(os.environ["Q1_WORK_MS"])
    conn = connect()
    channel = conn.channel()
    _declare(channel)
    channel.confirm_delivery()
    sent = 0
    for body in build_messages(tag, n, work_ms):
        channel.basic_publish(exchange="", routing_key=WORK_QUEUE, body=body,
                              properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
        sent += 1
    conn.close()
    print(f"PRODUCED {sent}", flush=True)
    if sent != n:
        raise SystemExit(f"published {sent} of {n}")


def collect() -> None:
    """Drain the done queue and report the DISTINCT ids seen. Distinct, not
    total, so a redelivery (same id done twice) never looks like extra work and
    a drop can never hide. One line the runner parses: COLLECT {json}."""
    conn = connect()
    channel = conn.channel()
    _declare(channel)
    ids: set[str] = set()
    total = 0
    while True:
        method, _props, body = channel.basic_get(DONE_QUEUE, auto_ack=True)
        if method is None:
            break
        total += 1
        try:
            ids.add(json.loads(body)["id"])
        except (ValueError, KeyError, TypeError):
            pass
    conn.close()
    tag = os.environ.get("Q1_TAG")
    n = int(os.environ.get("Q1_N", "0"))
    expected = {f"{tag}-{i:05d}" for i in range(n)} if tag is not None else set()
    print("COLLECT " + json.dumps({"distinct": len(ids), "total": total, "duplicates": total - len(ids),
                                    "expected_done": len(ids & expected), "unexpected_done": len(ids - expected)}),
          flush=True)


def run() -> None:
    import pika

    stopper = Stopper()
    stopper.install()
    conn = connect()
    channel = conn.channel()
    _declare(channel)
    channel.confirm_delivery()  # publishes raise if not confirmed
    channel.basic_qos(prefetch_count=PREFETCH)
    log(f"consuming {WORK_QUEUE!r}, prefetch {PREFETCH}, publishing done to {DONE_QUEUE!r}")

    def publish_done(mid: str) -> None:
        try:
            channel.basic_publish(
                exchange="", routing_key=DONE_QUEUE,
                body=json.dumps({"id": mid}).encode(),
                properties=pika.BasicProperties(delivery_mode=2),  # persistent
                mandatory=True)
        except Exception as e:  # noqa: BLE001 - unconfirmed/unroutable -> requeue the work
            raise PublishFailed(str(e)) from None

    def publish_dead(body: bytes) -> None:
        try:
            channel.basic_publish(
                exchange="", routing_key=DEAD_QUEUE, body=body,
                properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
        except Exception as e:  # noqa: BLE001 - leave poison unacked if dead publish failed
            raise PublishFailed(str(e)) from None

    def do(msg: dict, sleep=time.sleep) -> None:
        do_unit(msg, publish_done, sleep=sleep)

    for method, _props, body in channel.consume(WORK_QUEUE, inactivity_timeout=1):
        if method is None:  # idle tick: honour a pending stop
            if stopper.stop.is_set():
                break
            continue
        action = decide(body, do)
        if action == "dead":
            try:
                publish_dead(body)
            except PublishFailed as e:
                log(f"dead publish not confirmed, requeueing poison: {e}")
                action = "requeue"
        if action in ("ack", "dead"):
            channel.basic_ack(method.delivery_tag)
        else:  # requeue: nack with requeue so it is redelivered now
            channel.basic_nack(method.delivery_tag, requeue=True)
        if stopper.stop.is_set():
            break

    try:
        channel.cancel()
        conn.close()
    except Exception:  # noqa: BLE001 - closing a dying connection
        pass
    log("stopped")


COMMANDS = {"consume": run, "produce": produce, "collect": collect}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "consume"
    if cmd not in COMMANDS:
        raise SystemExit(f"unknown command {cmd!r} (one of {', '.join(COMMANDS)})")
    sys.exit(COMMANDS[cmd]())
