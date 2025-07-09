#!/bin/bash

# Name of the service
SERVICE_NAME="champsim_scheduler"
SERVICE_FILE="${SERVICE_NAME}.service"
SYSTEMD_TARGET="/etc/systemd/system/${SERVICE_FILE}"

# Get the absolute path to the current working directory
CURRENT_DIR="$(pwd)"

# Get the path to the current Python interpreter (could be a virtualenv)
PYTHON_PATH="$(which python3)"

# Create a temporary file to hold the modified service file
TMP_SERVICE=$(mktemp)

# Replace placeholders in the template with actual paths using sed
# __WORKING_DIR__ → current working directory
# __PYTHON_PATH__ → full path to python3
# __SCRIPT_PATH__ → full path to job_scheduler.py
sed \
  -e "s|__WORKING_DIR__|${CURRENT_DIR}|g" \
  -e "s|__PYTHON_PATH__|${PYTHON_PATH}|g" \
  -e "s|__SCRIPT_PATH__|${CURRENT_DIR}/champsim_scheduler.py|g" \
  "${SERVICE_FILE}" > "${TMP_SERVICE}"

# Copy the modified service file to the systemd directory
sudo cp "${TMP_SERVICE}" "${SYSTEMD_TARGET}"

# Reload systemd to apply the new service definition
sudo systemctl daemon-reload

echo "✅ Service file installed to ${SYSTEMD_TARGET}"
