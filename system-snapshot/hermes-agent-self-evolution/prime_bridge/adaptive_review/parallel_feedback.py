"""Narrow adapter from review hooks to governed scheduler feedback contracts."""

from lapart_executor.adaptive_parallel import ParallelFeedback, assess_parallel_feedback


def adaptive_feedback_receipt(value):
    feedback = ParallelFeedback.from_mapping(value)
    execution, reviewer = assess_parallel_feedback(feedback)
    return {
        "feedback_sha256": feedback.canonical_sha256(),
        "execution_observation": execution.to_dict(),
        "reviewer_observation": reviewer.to_dict(),
        "execution_admission_hold": execution.hold_admission,
        "promotion_performed": False,
        "completion_authority": "parent_only",
    }
