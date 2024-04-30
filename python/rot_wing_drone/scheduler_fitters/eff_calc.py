import numpy as np

import matplotlib.pyplot as plt

eff_z_rudder = 3.93e-4 # da / dpprz
airspeed = 19 # m/s

d_rudder_d_pprz = -0.0018

Izz = 0.9752

dMzdpprz = eff_z_rudder * Izz

dMzdr = dMzdpprz / d_rudder_d_pprz

k_rudder2 = dMzdr / airspeed**2 * 10000

print("k_rudder[2] = " + str(k_rudder2))

u = np.arange(-9600, 9600)
mu = 10000
Wu = 0.0001
Wv_intial = 1.0
cost = Wu * u**2 + Wv_intial * (eff_z_rudder*u)**2 * mu

plt.figure()
plt.plot(u, cost)
plt.plot(u, Wv_intial * (eff_z_rudder*u)**2 * mu)
plt.xlabel(u)
plt.ylabel(cost)
plt.show()