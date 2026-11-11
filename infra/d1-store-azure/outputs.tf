output "account_name" {
  description = "The WORM storage account."
  value       = azurerm_storage_account.worm.name
}

output "account_id" {
  description = "Pass to the AKS root as -var=d1_store_account_id=..."
  value       = azurerm_storage_account.worm.id
}

output "container_name" {
  description = "The backup container."
  value       = azurerm_storage_container.backups.name
}

output "resource_group" {
  description = "The store's own resource group (not the lab's)."
  value       = azurerm_resource_group.store.name
}

output "retention" {
  description = "The default version-level retention the account enforces."
  value       = { state = var.immutability_state, days = 1 }
}
