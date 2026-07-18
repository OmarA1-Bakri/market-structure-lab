"""Secret-safe operator error messages shared by command-line entry points."""

from sqlalchemy.exc import SQLAlchemyError


def database_error_message(error: SQLAlchemyError) -> str:
    """Return a stable database failure label with an optional validated SQLSTATE."""
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    if not _is_sqlstate(sqlstate):
        return "database operation failed"
    return f"database operation failed (SQLSTATE {sqlstate})"


def _is_sqlstate(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 5
        and all(character.isdigit() or "A" <= character <= "Z" for character in value)
    )


__all__ = ["database_error_message"]
