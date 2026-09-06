"""Telegram backup backend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# Preserve the existing module implementation below while exposing request errors.
