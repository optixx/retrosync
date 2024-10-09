#!/bin/bash

if ! command -v python3 &>/dev/null; then
  echo "Python is not installed. Please install Python and try again."
  exit 1
fi

python3 -m venv venv

source venv/bin/activate
echo "Installing required Python packages..."
pip3 install -r requirements.txt

echo "Setup complete.""
