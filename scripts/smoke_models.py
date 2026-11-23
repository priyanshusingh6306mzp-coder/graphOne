import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.models import Startup, Source, StartupContent
from src.export.flatten import to_row, COLUMNS

s = Startup(source=Source(name="YC", url="https://www.ycombinator.com/companies/openai"),
            content=StartupContent(entityName="OpenAI"))
print(COLUMNS["STARTUP"])
print(to_row(s.model_dump(mode="json"), "STARTUP"))