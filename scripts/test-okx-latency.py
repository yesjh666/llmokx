#!/usr/bin/env python3
"""
测试本机到 OKX API 的网络延迟
"""

import requests
import time

# OKX API 端点
OKX_ENDPOINTS = {
    "公开 API (AWS)": "https://www.okx.com",
    "公开 API (阿里云)": "https://www.okx.com",
    "Web3 API": "https://www.okx.com",
}

# 测试 URL
TEST_URLS = [
    ("公开 API v5", "https://www.okx.com/api/v5/public/time"),
    ("Web3 行情", "https://www.okx.com/api/v5/market/index-tickers?instId=BTC-USDT"),
    ("Web3 Token", "https://www.okx.com/api/v5/market/tickers?instType=SPOT"),
]

def test_latency(name, url):
    """测试单次请求延迟"""
    try:
        start = time.time()
        response = requests.get(url, timeout=10)
        end = time.time()
        
        latency_ms = (end - start) * 1000
        status = response.status_code
        
        return {
            "name": name,
            "latency_ms": latency_ms,
            "status": status,
            "error": None
        }
    except Exception as e:
        return {
            "name": name,
            "latency_ms": None,
            "status": None,
            "error": str(e)
        }

def test_multiple(name, url, count=5):
    """多次测试取平均"""
    results = []
    for i in range(count):
        result = test_latency(name, url)
        if result["latency_ms"]:
            results.append(result["latency_ms"])
        time.sleep(0.5)
    
    if results:
        return {
            "name": name,
            "url": url,
            "min": min(results),
            "max": max(results),
            "avg": sum(results) / len(results),
            "count": len(results),
            "error": None
        }
    else:
        return {
            "name": name,
            "url": url,
            "min": None,
            "max": None,
            "avg": None,
            "count": 0,
            "error": "所有请求失败"
        }

def main():
    print("=" * 60)
    print("🌐 OKX API 延迟测试")
    print("=" * 60)
    print()
    
    results = []
    
    for name, url in TEST_URLS:
        print(f"测试：{name}")
        print(f"URL: {url}")
        
        result = test_multiple(name, url, count=5)
        results.append(result)
        
        if result["avg"]:
            print(f"  ✅ 成功 {result['count']}/5 次")
            print(f"  📊 最小：{result['min']:.2f} ms")
            print(f"  📊 最大：{result['max']:.2f} ms")
            print(f"  📊 平均：{result['avg']:.2f} ms")
        else:
            print(f"  ❌ 失败：{result['error']}")
        
        print()
    
    # 总结
    print("=" * 60)
    print("📈 测试总结")
    print("=" * 60)
    
    success = [r for r in results if r["avg"]]
    failed = [r for r in results if not r["avg"]]
    
    print(f"成功：{len(success)}/{len(results)}")
    print(f"失败：{len(failed)}/{len(results)}")
    
    if success:
        avg_all = sum(r["avg"] for r in success) / len(success)
        print()
        print(f"🎯 平均延迟：{avg_all:.2f} ms")
        
        # 评级
        if avg_all < 100:
            rating = "⭐⭐⭐⭐⭐ 优秀"
        elif avg_all < 200:
            rating = "⭐⭐⭐⭐ 良好"
        elif avg_all < 500:
            rating = "⭐⭐⭐ 一般"
        else:
            rating = "⭐⭐ 较差"
        
        print(f"📊 评级：{rating}")
    
    print()
    print("=" * 60)

if __name__ == "__main__":
    main()
