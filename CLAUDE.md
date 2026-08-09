## Writing plans

For any project that is a discrete local Git repository, you will find a
`doc/plans/` directory. If you do not find one, you may create it.

Within this directory, we write our dev plans in markdown files with names that
are prefixed with the year-and-month of creation. Some example file names:

* 2026-01-logging-refactor.md
* 2026-01-token-exchange-authentication.md

When entering plan mode, the system suggests a plan file with a random name.
Ignore the random name. Instead, create the plan file directly at the correct
`YYYY-MM-short-description.md` path in `doc/plans/` using the Write tool.

The first heading should be "Plan: " followed by the name in _Title Case_,
followed by a "Status:" callout. eg:

```
Plan: Token Exchange Authentication
===============================================================================

> Status: Planning

…
```

Valid statuses include: Planning, Underway, Complete.

Feel free to use Mermaid diagrams in plans to explain concepts and flows
visually.

When marking a plan as Complete, replace implementation code blocks with a
short prose summary of the functional/visual change. Don't duplicate code
that's now in the codebase. Leave diagrammatic code-blocks in place.

Once marked as "Complete", the plan can be moved into the doc/plans/archive/
subdirectory.
