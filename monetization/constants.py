"""Storage unit constants for the subscription plan catalog (BE-01)."""

BYTES_PER_KB = 1024
BYTES_PER_MB = 1024**2
BYTES_PER_GB = 1024**3

# BE-02: subscription lifecycle defaults (used only as fallbacks;
# the Free plan row's own trial_days / storage_bytes win when set).
TRIAL_DAYS_DEFAULT = 15
PURGE_GRACE_DAYS = 2
FREE_QUOTA_BYTES_DEFAULT = 100 * BYTES_PER_MB