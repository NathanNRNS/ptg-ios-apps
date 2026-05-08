#!/bin/bash
# Trigger an Xcode Cloud build by pushing a `build/<slug>` branch.
# Usage: ./scripts/xcode-cloud-trigger.sh <slug> [<slug>...]
#        ./scripts/xcode-cloud-trigger.sh --batch2  # all 41 wave2 apps
#        ./scripts/xcode-cloud-trigger.sh --all     # all 70 apps
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

case "${1:-}" in
    --batch2)
        SLUGS=(473-postal-exam act ase boating canada-citizenship ccht cda chauffeur clb cpr dmv ekg epa esl f-02 forklift fsc g1 hha hiset language-proficiency lpn mace moca nccco notary-public nremt parapro pca pert ptcb pte ramsay rbt rma snhd tabe toefl tsi wonderlic workkeys)
        ;;
    --all)
        SLUGS=($(node -e "console.log(Object.keys(require('./apps.json')).join(' '))"))
        ;;
    "")
        echo "Usage: $0 <slug> [<slug>...]   |   $0 --batch2   |   $0 --all"
        exit 1
        ;;
    *)
        SLUGS=("$@")
        ;;
esac

# Make sure we're on main + up to date
git fetch origin main
CURRENT_SHA=$(git rev-parse origin/main)
echo "Triggering ${#SLUGS[@]} apps from main @ ${CURRENT_SHA:0:8}"

for slug in "${SLUGS[@]}"; do
    BRANCH="build/$slug"
    echo "==> $slug"
    # Force-update branch to current main so Xcode Cloud picks up latest
    git push origin "$CURRENT_SHA:refs/heads/$BRANCH" --force
done

echo "Done. Watch Xcode Cloud builds at: https://appstoreconnect.apple.com/teams/<team>/apps"
