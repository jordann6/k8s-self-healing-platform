"""Q1 queue worker: parsing, the ack decision, and the work-then-confirm order,
tested without a broker and without pika installed.

The capability is queue-driven scaling with no lost work. KEDA does the
scaling; the no-lost-work guarantee is the worker's ack ordering, so that is
what is pinned here:

  * a unit of work is done BEFORE the id is published to the done queue, so a
    kill mid-work never reaches the done queue,
  * a delivery is acked only when the done publish is confirmed; an unconfirmed
    publish returns "requeue", never "ack", so the message is redelivered
    (at-least-once: a drop can never hide),
  * a message that is not a unit of work is poison: "dead", acked away, never
    requeued forever (which would wedge the queue and the KEDA scale signal).

Run: make test
"""
import unittest

import worker
from worker import BadMessage, PublishFailed, build_messages, decide, do_unit, parse_message


class Recorder:
    """Records the order of work and publish, and can fail the publish."""

    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def sleep(self, seconds):
        self.events.append(("work", seconds))

    def publish_done(self, mid):
        self.events.append(("publish", mid))
        if self.fail:
            raise PublishFailed("broker said no")


class ParseTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_message(b'{"id": "q1-0007", "work_ms": 250}'),
                         {"id": "q1-0007", "work_ms": 250})

    def test_work_ms_defaults_to_zero(self):
        self.assertEqual(parse_message(b'{"id": "q1-1"}'), {"id": "q1-1", "work_ms": 0})

    def test_oversized(self):
        big = b'{"id": "q1-1", "pad": "' + b"x" * (worker.MAX_BODY + 10) + b'"}'
        with self.assertRaises(BadMessage):
            parse_message(big)

    def test_not_json(self):
        with self.assertRaises(BadMessage):
            parse_message(b"not json")

    def test_not_an_object(self):
        with self.assertRaises(BadMessage):
            parse_message(b'["q1-1"]')

    def test_bad_ids(self):
        for body in (b'{"work_ms": 1}', b'{"id": ""}', b'{"id": 5}',
                     b'{"id": "has space"}', b'{"id": "bad/slash"}'):
            with self.assertRaises(BadMessage, msg=body):
                parse_message(body)

    def test_bad_work_ms(self):
        for body in (b'{"id": "q1-1", "work_ms": -1}',
                     b'{"id": "q1-1", "work_ms": 99999999}',
                     b'{"id": "q1-1", "work_ms": 1.5}',
                     b'{"id": "q1-1", "work_ms": true}',
                     b'{"id": "q1-1", "work_ms": "250"}'):
            with self.assertRaises(BadMessage, msg=body):
                parse_message(body)


class DoUnitTests(unittest.TestCase):
    def test_work_before_publish(self):
        r = Recorder()
        do_unit({"id": "q1-1", "work_ms": 200}, r.publish_done, sleep=r.sleep)
        self.assertEqual([e[0] for e in r.events], ["work", "publish"])
        self.assertEqual(r.events[0], ("work", 0.2))
        self.assertEqual(r.events[1], ("publish", "q1-1"))

    def test_publish_failure_propagates_after_work(self):
        r = Recorder(fail=True)
        with self.assertRaises(PublishFailed):
            do_unit({"id": "q1-1", "work_ms": 50}, r.publish_done, sleep=r.sleep)
        # Work still happened before the failed publish: the kill-equivalent
        # (publish never confirmed) leaves the message to requeue, not lost.
        self.assertEqual([e[0] for e in r.events], ["work", "publish"])


class DecideTests(unittest.TestCase):
    def _do(self, fail=False):
        rec = Recorder(fail=fail)
        return rec, (lambda msg, sleep: do_unit(msg, rec.publish_done, sleep=sleep))

    def test_valid_acks(self):
        rec, do = self._do()
        self.assertEqual(decide(b'{"id": "q1-1", "work_ms": 10}', do, sleep=rec.sleep), "ack")
        self.assertIn(("publish", "q1-1"), rec.events)

    def test_poison_is_dead_not_requeued(self):
        rec, do = self._do()
        self.assertEqual(decide(b"garbage", do, sleep=rec.sleep), "dead")
        # Poison is never run as work.
        self.assertEqual(rec.events, [])

    def test_unconfirmed_publish_requeues(self):
        rec, do = self._do(fail=True)
        self.assertEqual(decide(b'{"id": "q1-1", "work_ms": 10}', do, sleep=rec.sleep), "requeue")


class BuildMessagesTests(unittest.TestCase):
    def test_count_and_shape(self):
        msgs = build_messages("q1b-42", 3, 250)
        self.assertEqual(len(msgs), 3)
        decoded = [parse_message(m) for m in msgs]  # every message the producer builds is valid work
        self.assertEqual([m["id"] for m in decoded], ["q1b-42-00000", "q1b-42-00001", "q1b-42-00002"])
        self.assertTrue(all(m["work_ms"] == 250 for m in decoded))

    def test_ids_are_distinct(self):
        ids = {parse_message(m)["id"] for m in build_messages("t", 500, 10)}
        self.assertEqual(len(ids), 500)


class StopperTests(unittest.TestCase):
    def test_handle_sets_event(self):
        s = worker.Stopper()
        self.assertFalse(s.stop.is_set())
        s._handle()
        self.assertTrue(s.stop.is_set())


if __name__ == "__main__":
    unittest.main()
