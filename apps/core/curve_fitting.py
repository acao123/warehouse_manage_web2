import numpy as np
from scipy.optimize import curve_fit

def fit_function(Dn, m, a, b):
    return m * (1 - np.exp(-a * np.power(Dn, b)))

Dn_list = [
    0.61359, 1.61462, 2.60801, 3.60905, 4.60244,
    5.60347, 6.55865, 7.59789, 8.56072, 9.59232,
    10.59335, 11.54853, 12.58777, 29.99504
]
Pf_list = [
    0.01065, 0.01065, 0.02988, 0.10626, 0.15494,
    0.18428, 0.19921, 0.22437, 0.21312, 0.23234,
    0.29492, 0.32478, 0.31251, 0.33337
]

Dn_array = np.array(Dn_list)
Pf_array = np.array(Pf_list)

# 推荐的初始值
initial_guess = [0.37, 0.1, 1.0]

popt, pcov = curve_fit(
    fit_function,
    Dn_array,
    Pf_array,
    p0=initial_guess,
    bounds=([0, 0, 0], [1, 10, 10]),
    maxfev=10000
)

m_fit, a_fit, b_fit = popt
print(f"拟合参数: m={m_fit:.6f}, a={a_fit:.6f}, b={b_fit:.6f}")

# 计算 R²
Pf_fitted = fit_function(Dn_array, *popt)
ss_res = np.sum((Pf_array - Pf_fitted) ** 2)
ss_tot = np.sum((Pf_array - np.mean(Pf_array)) ** 2)
r_squared = 1 - ss_res / ss_tot
print(f"R² = {r_squared:.6f}")