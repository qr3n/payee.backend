# Instructions for Claude Code & AI Coding Assistants

This repository uses **AGENTS.md** as the single source of truth for all AI agent guidelines, Vertical Slice Architecture rules, verification commands, and git commit protocols.

Please read and strictly adhere to:
- [`AGENTS.md`](AGENTS.md)

### Key Highlights:
1. **Vertical Slice Architecture:** All features live in `services/api/app/modules/<domain>/`.
2. **Atomic Commits:** Follow the **Plan → Implement → Verify → Commit** cycle for every milestone.
3. **Quality Gate:** Run `make format && make check` before ANY commit. Code must be 100% green.
4. **Conventional Commits:** Use `<type>(<scope>): <imperative summary>`.
