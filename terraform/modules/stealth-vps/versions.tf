terraform {
  required_version = ">= 1.5.0"
  # No required_providers: this module is pure templatefile() with no
  # cloud-side resources. The cloud-init string is the only output; the
  # caller passes it to whatever provider creates the actual server.
}
