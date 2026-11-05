# A VNet the lab owns. AKS does not touch NSGs on a custom subnet, and the
# support policy allows customising them only there (not on node NICs), so
# this NSG is both the node firewall and the target of row S4.
#
# Bring your own CNI: pods get addresses from Cilium's cluster pool
# (var pod_cidr below, also given to AKS) and travel between nodes in VXLAN,
# so the subnet only needs room for nodes and upgrade surge.

locals {
  vnet_cidr    = "10.42.0.0/16"
  nodes_cidr   = "10.42.0.0/22"
  pod_cidr     = "10.244.0.0/16"
  service_cidr = "10.96.0.0/16"
  dns_ip       = "10.96.0.10"
}

resource "azurerm_virtual_network" "lab" {
  name                = "vnet-${local.name}"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  address_space       = [local.vnet_cidr]
  tags                = local.tags
}

resource "azurerm_subnet" "nodes" {
  name                 = "snet-${local.name}-nodes"
  resource_group_name  = azurerm_resource_group.lab.name
  virtual_network_name = azurerm_virtual_network.lab.name
  address_prefixes     = [local.nodes_cidr]
  # Egress goes through the cluster's load balancer, never the implicit
  # default outbound address.
  default_outbound_access_enabled = false

  # Pods leave the node masqueraded to its address, so the subnet is how Key
  # Vault and the evidence account admit the cluster. Storage ignores IP
  # rules for requests from its own region (ADR-0008), so the egress IP
  # cannot be used there.
  service_endpoint {
    service = "Microsoft.KeyVault"
  }
  service_endpoint {
    service = "Microsoft.Storage"
  }
}

resource "azurerm_network_security_group" "nodes" {
  name                = "nsg-${local.name}-nodes"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

# No SSH from anywhere, the VNet included (nodes are reached through Run
# Command only). The default rules already deny the internet; this makes the
# intent explicit and sits above AllowVnetInBound.
resource "azurerm_network_security_rule" "deny_ssh" {
  name                        = "deny-ssh-all"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.nodes.name
}

resource "azurerm_network_security_rule" "deny_rdp" {
  name                        = "deny-rdp-all"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3389"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.lab.name
  network_security_group_name = azurerm_network_security_group.nodes.name
}

resource "azurerm_subnet_network_security_group_association" "nodes" {
  subnet_id                 = azurerm_subnet.nodes.id
  network_security_group_id = azurerm_network_security_group.nodes.id
}
