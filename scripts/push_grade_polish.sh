#!/usr/bin/env bash
# Run from an authenticated shell (the one where `git push` already works).
set -euo pipefail
cd "$(dirname "$0")/.."

git push -u origin feat/grade-polish

echo
echo "Open this compare URL to create the PR in the GitHub UI:"
echo "https://github.com/vidhikansara26/w7_mcp/compare/main...feat/grade-polish?expand=1"
echo
echo "Paste title/body from docs/submission_assets/PR_BODY.md"
echo "After Actions goes green, screenshot → docs/submission_assets/ci_green.png"
echo "then: python assemble_submission.py --pdf && git add docs/submission_assets/ci_green.png submission && git commit -m 'chore: embed real Actions CI screenshot' && git push"
