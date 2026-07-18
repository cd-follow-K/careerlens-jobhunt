"""CareerLens Streamlit entry point.

Public imports are kept here so the regression tests and existing launch command
remain compatible after the codebase was split by responsibility.
"""

from career_lens.common import *
from career_lens.auth import *
from career_lens.storage import *
from career_lens.sources import *
from career_lens.ai_client import *
from career_lens.ai_client import _is_quota_exhausted_error
from career_lens.research import *
from career_lens.ai_analysis import *
from career_lens.verification import *
from career_lens.scheduler import *
from career_lens.ui import *


if __name__ == "__main__":
    main()
