"""Coverpoint summary for stream_dut. See tb_top.cp_registry."""
COVERPOINTS = [
  {
    "name": "cp_payload",
    "bins": [
      "BIN_ZERO",
      "BIN_MAX",
      "BIN_MIX"
    ]
  },
  {
    "name": "cp_backpressure",
    "bins": [
      "BIN_NO_BP",
      "BIN_BURSTY_BP"
    ]
  },
  {
    "name": "cp_idle",
    "bins": [
      "BIN_ACTIVE",
      "INACTIVE"
    ]
  }
]
