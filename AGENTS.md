# AGENTS.md

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) for every
commit.

### Format

```text
<type>(<optional scope>): <short description>

<optional body>

Co-authored-by: DeepSeek AI <noreply@deepseek.com>
```

### Rules

- Use a lowercase type: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`,
  `perf`, `build`, or `ci`.
- Add a scope only when it clarifies the affected area, for example
  `fix(ui): ...` or `docs(readme): ...`.
- Keep the description imperative, concise, and lowercase where natural.
- Do not end the subject line with a period.
- Add a body when the change is non-trivial; explain what changed and why.
- Keep commits focused on one logical change.
- Always include this trailer in every commit:

```text
Co-authored-by: DeepSeek AI <noreply@deepseek.com>
```

### Examples

```text
feat: implement live massacre mission tracker
```

```text
fix(ui): show only commander names in the UI
```

```text
docs: add commit conventions for agents

Co-authored-by: DeepSeek AI <noreply@deepseek.com>
```
