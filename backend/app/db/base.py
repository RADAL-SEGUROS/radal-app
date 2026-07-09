"""Import Base + ALL models so metadata is complete for create_all()."""
from app.models.base_class import Base  # noqa: F401

# Importing the models package registers every table on Base.metadata.
import app.models  # noqa: F401,E402

__all__ = ["Base"]
