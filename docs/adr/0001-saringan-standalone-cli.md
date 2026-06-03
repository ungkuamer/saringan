# Saringan is a standalone CLI with its own configuration

Saringan is implemented as a standalone CLI that validates a target repository state using its own canonical `saringan.toml` configuration file. Rangkai may invoke Saringan as an optional integration point, but Rangkai does not own Saringan's checks or configuration because Saringan must remain orchestrator-agnostic and independently runnable by humans, CI, or other callers.
