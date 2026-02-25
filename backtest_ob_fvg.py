# -*- coding: utf-8 -*-
"""
Order Block + FVG 백테스팅 엔진

M15 + M1 데이터를 사용한 완전 백테스팅:
- 1분봉 M1 데이터에서 신호 생성 (M15는 방향 결정)
- 실시간 청산 규칙 적용
- 성과 분석
"""

import sys
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import warnings

# Windows 콘솔 UTF-8 출력 설정
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from ob_fvg_strategy import OrderBlockFVGStrategy
from config import BACKTEST_CONFIG, STRATEGY_DEFAULTS

warnings.filterwarnings('ignore')

# 디렉토리 자동 생성
Path(BACKTEST_CONFIG["data_dir"]).mkdir(parents=True, exist_ok=True)
Path(BACKTEST_CONFIG["output_dir"]).mkdir(parents=True, exist_ok=True)


class BacktestEngine:
    """Order Block + FVG 백테스팅 엔진"""

    def __init__(
        self,
        strategy: OrderBlockFVGStrategy,
        max_bars_per_trade: int = None,
    ):
        """
        Args:
            strategy: OrderBlockFVGStrategy 인스턴스
            max_bars_per_trade: 최대 홀딩 바 수 (기본: config.py의 STRATEGY_DEFAULTS)
        """
        self.strategy = strategy
        self.max_bars_per_trade = max_bars_per_trade if max_bars_per_trade is not None else STRATEGY_DEFAULTS["max_bars_per_trade"]
        self.trades: List[Dict] = []
        self.pip_size = strategy.pip_size  # 심볼별 pip_size 사용
    
    def load_data(self, csv_file: str, timeframe: str = "M1") -> pd.DataFrame:
        """
        CSV 데이터 로드
        
        예상 컬럼: time, open, high, low, close, tick_volume
        """
        df = pd.read_csv(csv_file, parse_dates=['time'])
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values('time').reset_index(drop=True)
        return df
    
    def aggregate_to_m15(self, m1_df: pd.DataFrame) -> pd.DataFrame:
        """M1 데이터를 M15로 변환"""
        m1_df = m1_df.copy()
        
        # 15분 단위로 그룹화
        m1_df['m15_time'] = m1_df['time'].dt.floor('15min')
        
        m15_df = m1_df.groupby('m15_time').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'tick_volume': 'sum'
        }).reset_index()
        
        m15_df.rename(columns={'m15_time': 'time'}, inplace=True)
        m15_df['time'] = pd.to_datetime(m15_df['time'])
        
        return m15_df
    
    def run_backtest(self, m1_csv: str) -> Dict:
        """
        백테스팅 실행
        
        Args:
            m1_csv: M1 데이터 CSV 파일 경로
        
        Returns:
            성과 분석 결과
        """
        print(f"📊 Loading data from {m1_csv}...")
        m1_df = self.load_data(m1_csv)
        print(f"✓ Loaded {len(m1_df)} M1 bars")
        
        # M15 생성
        m15_df = self.aggregate_to_m15(m1_df)
        print(f"✓ Generated {len(m15_df)} M15 bars")
        
        self.trades = []
        active_trade = None
        
        print(f"\n🚀 Starting backtest...")
        print("=" * 80)
        
        # 각 M1 봉마다 실행
        for i in range(1, len(m1_df)):
            m1_current = m1_df.iloc[i]
            m1_time = m1_current['time']
            
            # M15 바 선택 (현재 M1의 시간에 해당하는 M15)
            m15_floor = m1_time.floor('15min')
            m15_bars = m15_df[m15_df['time'] <= m15_floor].copy()
            
            if len(m15_bars) < 2:
                continue
            
            # 현재까지의 M1 바
            m1_bars = m1_df.iloc[:i+1].copy()
            
            # 1. 활성 포지션이 없으면 신호 탐지
            if active_trade is None:
                signal = self.strategy.get_entry_signal(m15_bars, m1_bars)
                
                if signal is not None:
                    active_trade = {
                        'entry_time': signal['entry_time'],
                        'entry_price': signal['entry_price'],
                        'signal': signal['signal'],
                        'stop_loss': signal['stop_loss'],
                        'take_profit': signal['take_profit'],
                        'risk_pips': signal['risk_pips'],
                        'entry_bar_idx': i,
                        'bars_held': 0
                    }
                    print(f"\n✓ ENTRY @ {m1_time.strftime('%Y-%m-%d %H:%M')} | "
                          f"{self.strategy.format_signal(signal)}")
            
            # 2. 활성 포지션이 있으면 청산 규칙 확인
            if active_trade is not None:
                active_trade['bars_held'] = i - active_trade['entry_bar_idx']
                exit_price = m1_current['close']
                high = m1_current['high']
                low = m1_current['low']
                
                exit_signal = None
                
                # SL 확인 (우선순위 1)
                if active_trade['signal'] == 1:  # BUY
                    if low <= active_trade['stop_loss']:
                        exit_price = active_trade['stop_loss']
                        exit_signal = 'SL'
                else:  # SELL
                    if high >= active_trade['stop_loss']:
                        exit_price = active_trade['stop_loss']
                        exit_signal = 'SL'
                
                # TP 확인 (우선순위 2)
                if exit_signal is None:
                    if active_trade['signal'] == 1:  # BUY
                        if high >= active_trade['take_profit']:
                            exit_price = active_trade['take_profit']
                            exit_signal = 'TP'
                    else:  # SELL
                        if low <= active_trade['take_profit']:
                            exit_price = active_trade['take_profit']
                            exit_signal = 'TP'
                
                # 최대 홀딩 바 초과 (우선순위 3)
                if exit_signal is None and active_trade['bars_held'] >= self.max_bars_per_trade:
                    exit_signal = 'TIMEOUT'
                
                # 청산 실행
                if exit_signal is not None:
                    gross_pips, net_pips, _ = self.strategy.calculate_pnl(
                        active_trade['entry_price'],
                        exit_price,
                        active_trade['signal'],
                        active_trade['risk_pips']
                    )
                    
                    trade_record = {
                        'entry_time': active_trade['entry_time'],
                        'exit_time': m1_time,
                        'direction': 'BUY' if active_trade['signal'] == 1 else 'SELL',
                        'entry_price': active_trade['entry_price'],
                        'exit_price': exit_price,
                        'stop_loss': active_trade['stop_loss'],
                        'take_profit': active_trade['take_profit'],
                        'risk_pips': active_trade['risk_pips'],
                        'gross_pips': gross_pips,
                        'net_pips': net_pips,
                        'bars_held': active_trade['bars_held'],
                        'exit_reason': exit_signal,
                        'profit_loss': 'WIN' if net_pips > 0 else 'LOSS'
                    }
                    
                    self.trades.append(trade_record)
                    
                    print(f"✗ EXIT @ {m1_time.strftime('%Y-%m-%d %H:%M')} | "
                          f"{exit_signal} @ {exit_price:.5f} | "
                          f"P&L: {net_pips:+.2f}p (gross {gross_pips:+.2f}p)")
                    
                    active_trade = None
        
        print("\n" + "=" * 80)
        print(f"✓ Backtest completed | Total trades: {len(self.trades)}")
        
        # 성과 분석
        results = self.analyze_performance()
        
        return results
    
    def analyze_performance(self) -> Dict:
        """성과 분석"""
        if len(self.trades) == 0:
            print("\n⚠️  No trades executed")
            return {}
        
        df_trades = pd.DataFrame(self.trades)
        
        # 기본 지표
        total_trades = len(df_trades)
        wins = (df_trades['profit_loss'] == 'WIN').sum()
        losses = (df_trades['profit_loss'] == 'LOSS').sum()
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        
        # 수익성
        total_net_pips = df_trades['net_pips'].sum()
        cumulative_pips = df_trades['net_pips'].cumsum()
        
        # 손익비
        tp_trades = df_trades[df_trades['exit_reason'] == 'TP']
        sl_trades = df_trades[df_trades['exit_reason'] == 'SL']
        
        if len(tp_trades) > 0:
            avg_win_pips = tp_trades['net_pips'].mean()
        else:
            avg_win_pips = 0
        
        if len(sl_trades) > 0:
            avg_loss_pips = abs(sl_trades['net_pips'].mean())
        else:
            avg_loss_pips = 0
        
        rrr = avg_win_pips / avg_loss_pips if avg_loss_pips > 0 else (
            np.inf if avg_win_pips > 0 else 0
        )
        
        # Drawdown
        max_cumulative = cumulative_pips.cummax()
        drawdown = cumulative_pips - max_cumulative
        max_drawdown = drawdown.min()
        
        # 시간대별 분석
        df_trades['entry_hour'] = df_trades['entry_time'].dt.hour
        hourly_stats = df_trades.groupby('entry_hour').agg({
            'net_pips': ['count', 'sum', 'mean'],
            'profit_loss': lambda x: (x == 'WIN').sum() / len(x) * 100
        }).round(2)
        
        results = {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate_%': round(win_rate, 2),
            'total_net_pips': round(total_net_pips, 2),
            'avg_win_pips': round(avg_win_pips, 2),
            'avg_loss_pips': round(avg_loss_pips, 2),
            'risk_reward_ratio': round(rrr, 2),
            'max_drawdown_pips': round(max_drawdown, 2),
            'tp_trades': len(tp_trades),
            'sl_trades': len(sl_trades),
            'timeout_trades': (df_trades['exit_reason'] == 'TIMEOUT').sum(),
            'avg_bars_held': round(df_trades['bars_held'].mean(), 1)
        }
        
        # 출력
        self._print_results(results, df_trades)
        
        return results
    
    def _print_results(self, results: Dict, df_trades: pd.DataFrame):
        """결과 출력"""
        print("\n" + "=" * 80)
        print("📈 PERFORMANCE SUMMARY")
        print("=" * 80)
        print(f"Total Trades:        {results['total_trades']}")
        print(f"Wins/Losses:         {results['wins']} / {results['losses']}")
        print(f"Win Rate:            {results['win_rate_%']:.2f}%")
        print(f"Total Net Pips:      {results['total_net_pips']:+.2f}p")
        print(f"Average Win:         {results['avg_win_pips']:+.2f}p")
        print(f"Average Loss:        {results['avg_loss_pips']:+.2f}p")
        print(f"Risk:Reward Ratio:   {results['risk_reward_ratio']:.2f}")
        print(f"Max Drawdown:        {results['max_drawdown_pips']:.2f}p")
        print(f"TP/SL/Timeout:       {results['tp_trades']}/{results['sl_trades']}/{results['timeout_trades']}")
        print(f"Avg Bars Held:       {results['avg_bars_held']}")
        print("=" * 80)
    
    def save_trades_csv(self, output_file: str = None):
        """거래 결과 CSV로 저장"""
        if len(self.trades) == 0:
            print("⚠️  No trades to save")
            return

        if output_file is None:
            symbol = self.strategy.symbol
            output_file = str(Path(BACKTEST_CONFIG["output_dir"]) / f"{symbol}_trades_result.csv")

        df_trades = pd.DataFrame(self.trades)
        df_trades.to_csv(output_file, index=False)
        print(f"✓ Trades saved to {output_file}")

    def plot_equity_curve(self, output_file: str = None):
        """자산 곡선 플롯 (선택)"""
        try:
            import matplotlib.pyplot as plt

            if len(self.trades) == 0:
                return

            if output_file is None:
                symbol = self.strategy.symbol
                output_file = str(Path(BACKTEST_CONFIG["output_dir"]) / f"{symbol}_equity_curve.png")

            df_trades = pd.DataFrame(self.trades)
            df_trades['cumulative_pips'] = df_trades['net_pips'].cumsum()

            plt.figure(figsize=(14, 6))
            plt.plot(df_trades['cumulative_pips'], linewidth=2)
            plt.xlabel('Trade Number')
            plt.ylabel('Cumulative Pips')
            plt.title(f'Equity Curve - {self.strategy.symbol} (Cumulative Net Pips)')
            plt.grid(True, alpha=0.3)
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"✓ Equity curve saved to {output_file}")
            plt.close()
        except ImportError:
            print("⚠️  Matplotlib not available for plotting")


def main():
    """메인 실행 함수"""

    parser = argparse.ArgumentParser(description="OB+FVG 백테스트")
    parser.add_argument(
        "--symbol",
        type=str,
        default="EURUSD",
        choices=["EURUSD", "USDJPY", "EURJPY", "XAUUSD"],
        help="거래 심볼 (기본: EURUSD)"
    )
    parser.add_argument(
        "--source",
        type=str,
        default="csv",
        choices=["csv", "db"],
        help="데이터 소스 (csv: CSV 파일, db: PostgreSQL)"
    )
    parser.add_argument("--start", type=str, default=None, help="시작일 (YYYY-MM-DD, db 모드)")
    parser.add_argument("--end", type=str, default=None, help="종료일 (YYYY-MM-DD, db 모드)")
    args = parser.parse_args()
    SYMBOL = args.symbol

    # 전략 초기화 (config.py의 기본값 사용)
    strategy = OrderBlockFVGStrategy(symbol=SYMBOL)

    # 백테스팅 엔진
    engine = BacktestEngine(strategy)

    if args.source == "db":
        # PostgreSQL에서 데이터 로드
        from db import Database
        db = Database()

        start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
        end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None

        print(f"Loading {SYMBOL} from PostgreSQL...")
        if start:
            print(f"  Start: {args.start}")
        if end:
            print(f"  End: {args.end}")

        m1_df = db.query_ohlcv(SYMBOL, start=start, end=end)

        if len(m1_df) == 0:
            print("No data found in DB. Run init_db.py --import-csv or fetch_histdata.py first.")
            return

        # DB 데이터로 백테스트 (CSV 파일 대신 DataFrame 직접 전달)
        data_file = str(Path(BACKTEST_CONFIG["output_dir"]) / f"_tmp_{SYMBOL}_db.csv")
        m1_df.to_csv(data_file, index=False)
        results = engine.run_backtest(data_file)

    else:
        # CSV 파일에서 데이터 로드 (기존 동작)
        data_file = str(Path(BACKTEST_CONFIG["data_dir"]) / f"{SYMBOL}_M1_data.csv")

        if not Path(data_file).exists():
            print(f"Data file not found: {data_file}")
            print(f"\nUse: python fetch_mt5_data.py --symbol {SYMBOL} --months 3")
            print(f"Or:  python backtest_ob_fvg.py --symbol {SYMBOL} --source db")
            return

        results = engine.run_backtest(data_file)

    # 결과 저장
    engine.save_trades_csv()
    engine.plot_equity_curve()

    print("\n✓ Backtest completed!")


def create_sample_data(bars: int = 1000) -> pd.DataFrame:
    """테스트용 샘플 데이터 생성"""
    times = pd.date_range('2024-01-01', periods=bars, freq='1min')
    
    np.random.seed(42)
    prices = 1.0800 + np.cumsum(np.random.randn(bars) * 0.00005)
    
    data = []
    for i, t in enumerate(times):
        open_price = prices[i]
        close_price = prices[i] + np.random.randn() * 0.00003
        high_price = max(open_price, close_price) + abs(np.random.randn()) * 0.00005
        low_price = min(open_price, close_price) - abs(np.random.randn()) * 0.00005
        volume = np.random.randint(100, 1000)
        
        data.append({
            'time': t,
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'tick_volume': volume
        })
    
    return pd.DataFrame(data)


if __name__ == '__main__':
    main()
