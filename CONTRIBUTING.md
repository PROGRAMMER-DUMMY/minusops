# Contributing to MinusOps

Thank you for contributing to MinusOps!

## Engineering Invariants

1. **Standard Library Only in core:** The core governance and generation engines rely strictly on Python standard library modules to guarantee portability and offline execution.
2. **Zero Unicode Emoji Rule:** All console messages, markdown files, logs, and UI components must not use unicode emojis to prevent encoding crashes on cp1252/Windows shells and screen-reader degradation.
3. **Plan-Hash Binding:** Any code that generates, gates, or executes infrastructure must be bound to a SHA256 plan hash.
4. **Test Coverage:** All new CLI subcommands, Terraform modules, and governance rules must include pytest suites in tests.

## Development Workflow

1. Fork and clone the repository.
2. Create a virtual environment: python -m venv .venv
3. Install dependencies: pip install -e . and pip install -r requirements.txt
4. Run tests: pytest
5. Verify environment health: minusctl doctor
