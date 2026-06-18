import pandas as pd
import numpy as np

def test_3way_arbitrage():
    print("=== УНИТ-ТЕСТ: 3-Way Arbitrage Logic ===")
    
    # Mock data for options
    data = {
        'expiry': ['24May24', '24May24', '24May24', '24May24'],
        'option_type': ['C', 'C', 'C', 'C'],
        'strike': [3000, 3100, 3200, 3300],
        'dte': [1.5, 1.5, 1.5, 1.5],
        'moneyness_cat': ['ATM', 'OTM', 'ITM', 'OTM'],
        'AEVO_mid': [100.0, 50.0, 200.0, 10.0],
        'DERIVE_mid': [112.0, 52.0, np.nan, 22.0], # Missing on DERIVE
        'BYBIT_mid': [125.0, 48.0, 225.0, 11.0],
        'spread_pct_aevo': [0.1, 0.4, 0.1, 0.1], # AEVO spread: 40% on strike 3100
        'spread_pct_deri': [0.1, 0.1, 0.1, 0.1],
        'spread_pct_bybit': [0.1, 0.1, 0.1, 0.1]
    }
    
    merged = pd.DataFrame(data)
    # Filter OTM spreads > 30%
    merged = merged[~((merged['moneyness_cat'] == 'OTM') & ((merged['spread_pct_aevo'] > 0.3) | (merged['spread_pct_deri'] > 0.3) | (merged['spread_pct_bybit'] > 0.3)))]
    
    exchanges = ['AEVO_mid', 'DERIVE_mid', 'BYBIT_mid']
    opportunities = []
    
    for i in range(len(exchanges)):
        for j in range(i+1, len(exchanges)):
            ex1 = exchanges[i]
            ex2 = exchanges[j]
            
            temp_diff = (merged[ex1] - merged[ex2]).abs()
            mask = (
                (temp_diff >= 10.0) &
                (merged['moneyness_cat'] != 'ITM') &
                ((merged['dte'] < 3) | (temp_diff >= 20.0)) &
                merged[ex1].notna() & merged[ex2].notna()
            )
            
            for _, row in merged[mask].iterrows():
                opportunities.append({
                    'strike': row['strike'],
                    'ex1': ex1,
                    'ex2': ex2,
                    'mid1': row[ex1],
                    'mid2': row[ex2],
                    'gap': temp_diff.loc[_]
                })
                
    print(f"Mock Data (filtered by OTM spread):")
    print(merged[['strike', 'AEVO_mid', 'DERIVE_mid', 'BYBIT_mid']])
    print("\nExpected: Strike 3000 has gap $12 between AEVO and DERIVE (100 vs 112).")
    print("Expected: Strike 3000 has gap $25 between AEVO and BYBIT (100 vs 125).")
    print("Expected: Strike 3000 has gap $13 between DERIVE and BYBIT (112 vs 125).")
    print("Expected: Strike 3100 was OTM with 40% spread on AEVO -> FILTERED OUT.")
    print("Expected: Strike 3200 is ITM -> FILTERED OUT.")
    print("Expected: Strike 3300 has gap $12 between AEVO and DERIVE (10 vs 22), and $11 between DERIVE and BYBIT.")
    print("-" * 40)
    
    if len(opportunities) == 5:
        print("✅ TEST PASSED: 5 opportunities found.")
        for opp in opportunities:
            print(f"  -> Found Gap ${opp['gap']:.1f} between {opp['ex1']} and {opp['ex2']} (Strike {opp['strike']})")
    else:
        print(f"❌ TEST FAILED: Found {len(opportunities)} opportunities, expected 5.")
        
if __name__ == "__main__":
    test_3way_arbitrage()
