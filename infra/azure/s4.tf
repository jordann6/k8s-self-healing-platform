# Row S4: the node firewall opened to the internet, closed by Azure Policy.
#
# A deployIfNotExists definition on NSG security rules: an inbound Allow rule
# whose source is the whole internet is not compliant, and the remediation
# re-writes that same rule with access Deny. DINE deploys on its own when a
# rule is written (after evaluationDelay); a remediation task is only needed
# for rules that were already open before the assignment, which the lab never
# has (ADR-0007, D5).
#
# Baseline vs hardened is the assignment's enforcement mode (ADR-0001 Azure
# addendum): DoNotEnforce evaluates and reports but never deploys. Terraform
# creates it enforced and ignores the field; the runner owns it in a session.
#
# Production puts prevention first (a Deny policy on the same condition).
# This row proves the detective layer that catches what prevention misses.

locals {
  open_sources = ["*", "0.0.0.0/0", "0.0.0.0", "Internet", "Any", "::/0"]
  rule_alias   = "Microsoft.Network/networkSecurityGroups/securityRules"
}

resource "azurerm_policy_definition" "close_open_ingress" {
  name         = "${local.name}-close-open-ingress"
  display_name = "Self-healing lab: close NSG rules open to the internet"
  description  = "Inbound Allow rules from any internet source are rewritten with access Deny."
  policy_type  = "Custom"
  # Security rules carry no tags or location; All, not Indexed, evaluates them.
  mode     = "All"
  metadata = jsonencode({ category = "Network" })

  policy_rule = jsonencode({
    "if" = {
      allOf = [
        { field = "type", equals = local.rule_alias },
        { field = "${local.rule_alias}/access", equals = "Allow" },
        { field = "${local.rule_alias}/direction", equals = "Inbound" },
        {
          anyOf = [
            { field = "${local.rule_alias}/sourceAddressPrefix", in = local.open_sources },
            {
              count = {
                field = "${local.rule_alias}/sourceAddressPrefixes[*]"
                where = { field = "${local.rule_alias}/sourceAddressPrefixes[*]", in = local.open_sources }
              }
              greater = 0
            },
          ]
        },
      ]
    }
    "then" = {
      effect = "deployIfNotExists"
      details = {
        type = local.rule_alias
        name = "[field('fullName')]"
        # As soon as the write that opened it has succeeded.
        evaluationDelay = "AfterProvisioningSuccess"
        existenceCondition = {
          field  = "${local.rule_alias}/access"
          equals = "Deny"
        }
        # Network Contributor, the least built-in role that writes a rule.
        roleDefinitionIds = ["/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"]
        deployment = {
          properties = {
            mode = "incremental"
            template = {
              "$schema"      = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
              contentVersion = "1.0.0.0"
              parameters = {
                ruleFullName       = { type = "string" }
                protocol           = { type = "string" }
                priority           = { type = "int" }
                sourcePortRange    = { type = "string" }
                destPrefix         = { type = "string" }
                destPortRange      = { type = "string" }
                destPortRanges     = { type = "array" }
                sourceAddressValue = { type = "string" }
              }
              resources = [{
                type       = local.rule_alias
                apiVersion = "2023-09-01"
                name       = "[parameters('ruleFullName')]"
                properties = {
                  description              = "Closed by policy ${local.name}-close-open-ingress (row S4)."
                  access                   = "Deny"
                  direction                = "Inbound"
                  protocol                 = "[parameters('protocol')]"
                  priority                 = "[parameters('priority')]"
                  sourcePortRange          = "[parameters('sourcePortRange')]"
                  sourceAddressPrefix      = "[parameters('sourceAddressValue')]"
                  destinationAddressPrefix = "[parameters('destPrefix')]"
                  destinationPortRange     = "[if(empty(parameters('destPortRanges')), parameters('destPortRange'), json('null'))]"
                  destinationPortRanges    = "[if(empty(parameters('destPortRanges')), json('[]'), parameters('destPortRanges'))]"
                }
              }]
            }
            parameters = {
              ruleFullName       = { value = "[field('fullName')]" }
              protocol           = { value = "[field('${local.rule_alias}/protocol')]" }
              priority           = { value = "[field('${local.rule_alias}/priority')]" }
              sourcePortRange    = { value = "[coalesce(field('${local.rule_alias}/sourcePortRange'), '*')]" }
              destPrefix         = { value = "[coalesce(field('${local.rule_alias}/destinationAddressPrefix'), '*')]" }
              destPortRange      = { value = "[coalesce(field('${local.rule_alias}/destinationPortRange'), '*')]" }
              destPortRanges     = { value = "[coalesce(field('${local.rule_alias}/destinationPortRanges'), json('[]'))]" }
              sourceAddressValue = { value = "*" }
            }
          }
        }
      }
    }
  })
}

resource "azurerm_resource_group_policy_assignment" "close_open_ingress" {
  name                 = "${local.name}-close-open-ingress"
  display_name         = "Self-healing lab: close NSG rules open to the internet (row S4)"
  resource_group_id    = azurerm_resource_group.lab.id
  policy_definition_id = azurerm_policy_definition.close_open_ingress.id
  location             = azurerm_resource_group.lab.location
  enforce              = true

  identity {
    type = "SystemAssigned"
  }

  lifecycle {
    # The runner switches DoNotEnforce (baseline) and Default (hardened).
    ignore_changes = [enforce]
  }
}

# Created by Terraform, not by the portal, so the role is granted by hand
# (ADR-0007 row 13: the docs say only the portal grants it automatically).
resource "azurerm_role_assignment" "policy_network_contributor" {
  scope                = azurerm_resource_group.lab.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_resource_group_policy_assignment.close_open_ingress.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}
