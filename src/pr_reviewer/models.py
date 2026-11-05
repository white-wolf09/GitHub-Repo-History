from dataclasses import dataclass, field

@dataclass
class PRContext:
    owner: str
    repo: str
    number: int
    title: str
    branch: str
    head_sha: str
    draft: bool = False

@dataclass
class Story:
    key: str
    title: str
    criteria: str

@dataclass
class Verdict:
    fulfilled: bool
    feedback: str

@dataclass
class Decision:
    action: str            # "merge" | "reject" | "skip"
    reason: str
    feedback: str = ""
    steps: list = field(default_factory=list)

@dataclass
class CommitContext:
    owner: str
    repo: str
    branch: str
    base: str          # default branch the commit would be merged into
    sha: str
    message: str = ""
