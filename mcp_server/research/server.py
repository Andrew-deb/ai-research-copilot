"""Launch the preserved Research server from a self-contained deployment root."""

import runpy

if __name__ == "__main__":
    runpy.run_module("research_mcp_server", run_name="__main__")
