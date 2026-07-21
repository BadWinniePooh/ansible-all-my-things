---
name: rewrite-commit-author
description: >
  Rewrite the author and committer identity on existing commits, for example
  after committing with the wrong git user.name or user.email. Use when asked
  to reset, fix, correct or replace the author of one or more commits, or to
  remove an email address from commit metadata.
---
# Rewrite commit author and committer identity

Replace the identity recorded on existing commits — typically after work was
committed with a wrong or unwanted `user.name` / `user.email`.

## Before touching anything

Rewriting produces **new commits with new SHAs**. The old commits are not
edited; they are replaced.

Establish these three facts first, and stop and ask if any is unclear:

1. **Have the target commits been pushed?** Run
   `git log --format='%h %ae' <base>..HEAD` and check for a remote branch with
   `git ls-remote --heads origin <branch>`. Rewriting unpushed local commits
   is safe. Rewriting pushed commits rewrites history other people may have
   pulled, and requires a force-push — never do it without explicit
   confirmation from the user for that specific consequence.
2. **Which commits actually carry the unwanted identity?** Do not trust the
   starting point given to you; verify it. Both fields matter:

   ```bash
   git log --format='%h | %an <%ae> | %cn <%ce> | %s' <base>..HEAD
   ```

3. **What is the replacement identity?** Read the current configuration
   (`git config user.name`, `git config user.email`) and confirm it is the
   intended new value rather than assuming.

## Choose the rewrite range correctly

This is the step that causes real damage when rushed.

Use `main..HEAD` — commits reachable from the branch but not from the
integration branch — **not** `<first-bad-commit>~1..HEAD`.

If the branch contains a merge from `main`, the second form also sweeps in
every commit that merge brought along. Those commits get rewritten to new
SHAs, the branch permanently diverges from `main`, and every later merge or
rebase conflicts against duplicated history.

With `main..HEAD`, merge commits on the branch keep their second parent
pointing at the untouched real `main` commit.

## Rewrite both author and committer

The unwanted address is normally recorded in both fields. Rewriting only the
author leaves the address in history and defeats the purpose. Filter on each
field independently — a commit may have been authored by one identity and
committed by another.

## Procedure

The working tree must be clean; stash first if it is not, and restore
afterwards.

```bash
git branch backup-before-author-rewrite HEAD
git stash push -m "wip" -- <paths>          # only if the tree is dirty

FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --env-filter '
if [ "$GIT_AUTHOR_EMAIL" = "<old-email>" ]; then
  export GIT_AUTHOR_NAME="<new-name>"
  export GIT_AUTHOR_EMAIL="<new-email>"
fi
if [ "$GIT_COMMITTER_EMAIL" = "<old-email>" ]; then
  export GIT_COMMITTER_NAME="<new-name>"
  export GIT_COMMITTER_EMAIL="<new-email>"
fi
' -- main..HEAD

git stash pop                                # if a stash was created
```

`git filter-branch` preserves merge commits natively, which `git rebase`
does not without `--rebase-merges`. It is deprecated in favour of
`git-filter-repo`, but filter-repo is a separate install and refuses to run in
a repository that has other refs pointing at the rewritten history, so
filter-branch remains the pragmatic choice for a bounded, local range.

## Verify before reporting success

Run all three checks. Report the old-SHA to new-SHA mapping in the summary.

```bash
# 1. No trace of the old identity remains in the rewritten range
rtk proxy git log --format="%h %ae %ce" main..HEAD

# 2. Merge commits kept both parents; the second still points at real main
rtk proxy git log --format="%h [%p] %ae | %s" -n 8 HEAD

# 3. Content is untouched — this diff must be empty
git diff backup-before-author-rewrite HEAD
```

An empty diff in check 3 is the proof that only metadata changed. If it is
not empty, something rewrote content — reset to the backup branch and
investigate before doing anything else.

> [!NOTE]
> RTK's git filter truncates long log output and can hide merge lines, which
> makes a correct rewrite look broken. Use `rtk proxy git log ...` whenever
> parent SHAs or exact commit counts matter.

## Clean up the safety nets

Two references still point at the old commits after the rewrite, keeping the
old identity reachable in the repository:

- the manual backup branch, `backup-before-author-rewrite`
- filter-branch's own backup, `refs/original/refs/heads/<branch>`

Leave both in place until the user has reviewed the result, then delete them
only when asked:

```bash
git branch -D backup-before-author-rewrite
git update-ref -d refs/original/refs/heads/<branch>
```

Until both are gone, the old email is still present in the object database
and `git log --branches` still finds it.
