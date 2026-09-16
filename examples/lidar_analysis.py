import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
import matplotlib.gridspec as gridspec
import pandas as pd
from scipy.signal import savgol_filter

#====
def lin_interp(X_in, X_ref, Y_ref):
    """
    Simple linear interpolation with no dependance on scipy

    :param X_in: input values
    :type  X_in: float or array
    :param X_ref x values
    :type  X_ref: array
    :param Y_ref y values
    :type  Y_ref: array
    :return: y value linearly interpolated at ``X_in``
    """
    X_ref = np.array(X_ref)
    Y_ref = np.array(Y_ref)

    # Definition of the interpolating function
    def lin_oneElement(x, X_ref, Y_ref):
        if x < X_ref.min() or x > X_ref.max():
            return np.nan

        # Find closest left-hand size index
        n = np.argmin(np.abs(x-X_ref))
        if X_ref[n] > x or n == len(X_ref):
            n -= 1
        a = (Y_ref[n+1] - Y_ref[n])/(X_ref[n+1] - X_ref[n])
        b = Y_ref[n] - a*X_ref[n]
        return a*x + b

    # Wrapper to the function above
    if len(np.atleast_1d(X_in)) == 1:
        Y_out = lin_oneElement(X_in, X_ref, Y_ref)
    else:
        X_in = np.array(X_in)
        Y_out = np.zeros_like(X_in)
        for i, x_in in enumerate(X_in):
            Y_out[i] = lin_oneElement(x_in, X_ref, Y_ref)
    return Y_out
#===



my_path='/home/dusty/workspace/terraforming_mars/lidarmeasure/output/20260916T222655034331Z-measurement/'
with np.load(my_path+'/waterfall.npz') as data:
    t= data['time_edges_s']
    C=data['counts']
    dx_range=data['range_edges_m'][1]-data['range_edges_m'][0]

X=np.arange(0,C.shape[-1])*dx_range+dx_range/2 #Midpoints [m]


# Load the JSONL file into a DataFrame
df = pd.read_json(my_path+'/mount-coordinates.jsonl', lines=True)
# Convert the DataFrame (or specific columns) to a NumPy array
t_mount = df['started_unix_s'].values-df['started_unix_s'][0]
azi=lin_interp(t,t_mount,df['azimuth_deg'].values)
elev=lin_interp(t,t_mount,df['elevation_deg'].values)

bg_start= 30
bg_end = 300   # m, range window used for background
sg_window = 9  #Savitzky-Golay window (odd, bins) 
sg_order = 3   # Savitzky-Golay window poly order

def subtract_background(counts, r):
    """Mean of far-range bins (no signal) is the sky + dark background."""
    mask = (r >= bg_start) & (r <= bg_end)
    bg = counts[mask].mean()
    return counts - bg


def denoise_poisson(counts):
    """
    Anscombe transform stabilises Poisson variance (Anscombe 1948), then a
    Savitzky-Golay filter (Savitzky & Golay 1964), then inverse transform.
    """
    y = 2.0 * np.sqrt(np.clip(counts, 0.0, None) + 3.0 / 8.0)
    y_s = savgol_filter(y, sg_window, sg_order)
    return (y_s / 2.0) ** 2 - 3.0 / 8.0                # simple (biased) inverse

for ti in range(len(t)-1):
    C[ti,:]=subtract_background(C[ti,:], X) #Background substraction
    C[ti,:]=denoise_poisson(C[ti,:])        #De-noising
    #C[ti,:]=C[ti,:]*X**2                    #Range correction 


t_start=0
t_end=10
vmin=0
vmax=50
its= np.argmin(np.abs(t-t_start))
ite= np.argmin(np.abs(t-t_end))
if its==ite:ite+=1

t_slice=np.mean(C[its:ite,:],axis=0)
plt.close('all')
plt.figure(figsize=(14,10))

gs = gridspec.GridSpec(3,3,width_ratios=[2,1,1], height_ratios=[3 ,1,1])

ax=plt.subplot(gs[0:2,0])
plt.pcolormesh(t[0:-1],X,C.T, cmap='inferno',vmin=vmin,vmax=vmax)
plt.colorbar(orientation='horizontal')
plt.plot([t[its],t[its]],[0,50],':w',lw=1)
plt.plot([t[ite],t[ite]],[0,50],':w',lw=1)

plt.ylabel('X [m]')
ax.xaxis.set_major_locator(MultipleLocator(2))
ax.xaxis.set_minor_locator(MultipleLocator(0.25))
plt.xlim([0,14])
plt.ylim([0,30])

ax=plt.subplot(gs[2,0])

color = 'tab:red'
ax.set_xlabel('Time (s)')
ax.set_ylabel(r'Elevation $\phi$', color=color)
ax.plot(t, elev, color=color)
ax.tick_params(axis='y', labelcolor=color)

ax2 = ax.twinx()  # instantiate a second Axes that shares the same x-axis

color = 'tab:blue'
ax2.set_ylabel(r'Azimuth $\theta$', color=color)  # we already handled the x-label with ax1
ax2.plot(t, azi, color=color)
ax2.tick_params(axis='y', labelcolor=color)
ax.xaxis.set_major_locator(MultipleLocator(2))
ax.xaxis.set_minor_locator(MultipleLocator(0.25))
plt.xlabel('Time [s]')

ax=plt.subplot(gs[0,1:])
plt.step(X,t_slice)
plt.xlabel('X [m]')
plt.ylabel('Counts')
plt.xlim([0,50])

#Elevation
plt.grid(False)
ax=plt.subplot(gs[1:,1],projection='polar')
plt.pcolormesh(elev[its:ite]*np.pi/180,X,C[its:ite,:].T,cmap='inferno',vmin=vmin,vmax=vmax)
plt.colorbar()
ax.set_thetamin(-30)
ax.set_thetamax(10)
ax.set_rmax(5)
plt.xlabel('X [m]')

#Azimuth
plt.grid(False)
ax=plt.subplot(gs[1:,2],projection='polar')
plt.pcolormesh(azi[its:ite]*np.pi/180,X,C[its:ite,:].T,cmap='inferno',vmin=vmin,vmax=vmax)
plt.colorbar()
ax.set_thetamin(-30)
ax.set_thetamax(30)
ax.set_rmax(50)
ax.set_theta_zero_location("N")
ax.set_theta_direction(-1)

plt.tight_layout()
plt.show()
