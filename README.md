#!/bin/bash

# ═══════════════════════════════════════════════════════════════
#  AI Emergency Savior — Script de création du Monorepo
#  Fusionne les 3 repos existants dans un monorepo propre
# ═══════════════════════════════════════════════════════════════

set -e

MONOREPO_NAME="AI-Emergency-Savior"
GITHUB_USER="youssefhadjkacem"

echo "🚀 Création du monorepo $MONOREPO_NAME..."

# 1. Créer le dossier
mkdir -p $MONOREPO_NAME && cd $MONOREPO_NAME
git init
git checkout -b main

# 2. Créer la structure de base
mkdir -p frontend backend asr-finetuning docs

# 3. Copier le README
cp ../README.md .

# 4. Créer .gitignore
cat > .gitignore << 'EOF'
# Node
node_modules/
.next/
.env.local
.env

# Python
__pycache__/
*.pyc
venv/
.venv/
*.egg-info/

# Misc
.DS_Store
*.log
dist/
build/
EOF

# 5. Créer .env.example
cat > .env.example << 'EOF'
# ── Frontend ──────────────────────────────────────────────────
NEXT_PUBLIC_API_URL=http://localhost:8000

# ── HuggingFace (à configurer dans les Spaces Settings) ───────
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
api_key=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
EOF

# 6. Premier commit
git add .
git commit -m "🎉 init: création du monorepo AI-Emergency-Savior"

echo ""
echo "✅ Structure de base créée !"
echo ""
echo "📋 Prochaines étapes :"
echo "   1. Copier le code frontend dans ./frontend/"
echo "   2. Copier le code backend dans ./backend/"
echo "   3. Copier le fine-tuning ASR dans ./asr-finetuning/"
echo "   4. Créer le repo sur GitHub : https://github.com/new"
echo "   5. git remote add origin https://github.com/$GITHUB_USER/$MONOREPO_NAME.git"
echo "   6. git push -u origin main"
echo ""
echo "🔗 Liens à mettre à jour dans le README :"
echo "   - Membres de l'équipe complets"
echo "   - Screenshots / démo GIF dans docs/"
