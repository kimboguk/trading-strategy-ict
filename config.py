# -*- coding: utf-8 -*-
"""
ICT Trading Strategy - 전략 설정

심볼별 pip_size, 스프레드, 커미션 및 전략 기본 파라미터 관리
"""

# 심볼별 설정
SYMBOLS = {
    "EURUSD": {
        "pip_size": 0.0001,
        "spread_pips": 0.4,
        "commission_pips": 0.3,
        "sl_buffer_pips": 0.0001,   # 0.1 pip
    },
    "USDJPY": {
        "pip_size": 0.01,
        "spread_pips": 0.5,
        "commission_pips": 0.3,
        "sl_buffer_pips": 0.01,     # 0.1 pip (JPY 단위)
    },
    "EURJPY": {
        "pip_size": 0.01,
        "spread_pips": 0.6,
        "commission_pips": 0.3,
        "sl_buffer_pips": 0.01,
    },
}

# 전략 기본 파라미터
STRATEGY_DEFAULTS = {
    "risk_reward_ratio": 2.0,       # 손익비 기본값 (1.5~3.0 권장)
    "max_bars_per_trade": 100,      # 최대 홀딩 바 수
    "allowed_hours": {0, 1, 8, 9, 16, 17},  # 런던+미국 세션 (UTC)
    "require_fvg_confirm": True,    # OB+FVG 동시 확인 여부
}

# 백테스트 설정
BACKTEST_CONFIG = {
    "data_dir": "./data",
    "output_dir": "./outputs",
}

# 리스크 관리 한도
RISK_LIMITS = {
    "daily_max_loss_pips": 100,
    "weekly_max_loss_pips": 300,
    "monthly_max_loss_pips": 500,
    "max_drawdown_pct": 30.0,
}
