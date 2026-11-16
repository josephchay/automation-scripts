#!/usr/bin/env bash
set -euo pipefail

# Configuration
SOURCE_DIR="$HOME/Projects"
BACKUP_DIR="$HOME/Backups"
RETENTION_DAYS=7
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
ARCHIVE_NAME="backup_${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "Creating backup of $SOURCE_DIR..."
tar -czf "$BACKUP_DIR/$ARCHIVE_NAME" -C "$(dirname "$SOURCE_DIR")" "$(basename "$SOURCE_DIR")"
echo "Saved to $BACKUP_DIR/$ARCHIVE_NAME"

echo "Removing backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "backup_*.tar.gz" -type f -mtime +$RETENTION_DAYS -print -delete

echo "Backup cycle complete."
