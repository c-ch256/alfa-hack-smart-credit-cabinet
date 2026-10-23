"""Typed errors shared by validation, artifacts and policy."""
class ScoringError(ValueError):
    """Base domain error."""
class InputValidationError(ScoringError):
    pass
class SchemaVersionError(InputValidationError):
    pass
class OutOfDomainError(InputValidationError):
    pass
class ArtifactError(ScoringError):
    pass
