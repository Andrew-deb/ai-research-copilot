"""Compatibility module: Research retains its existing OpenAlex broker API."""
import sys
from shared_resource.brokers import openalex_broker
sys.modules[__name__] = openalex_broker
