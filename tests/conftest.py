import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Normal test runs use mocks and must stay independent of the private desktop configuration.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["CONTEXT_AGENT_TRACE_ENABLED"] = "0"
