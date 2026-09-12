import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
import matplotlib.gridspec as gridspec
import pandas as pd

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

with np.load('/home/akling/Data/jetson/waterfall.npz') as data:
    t= data['time_edges_s']
    C=data['counts']
    dx_range=data['range_edges_m'][1]-data['range_edges_m'][0]

DX=np.arange(0,C.shape[-1])*dx_range

# Load the JSONL file into a DataFrame

df = pd.read_json('/home/akling/Data/jetson/mount-coordinates.jsonl', lines=True)

# Convert the DataFrame (or specific columns) to a NumPy array

t_mount = df['started_unix_s'].values-df['started_unix_s'][0]
azi=lin_interp(t,t_mount,df['azimuth_deg'].values)
elev=lin_interp(t,t_mount,df['elevation_deg'].values)+np.arange(0,len(t))/len(t)*90

t_start= 18
t_end= 22
its= np.argmin(np.abs(t-t_start))
ite= np.argmin(np.abs(t-t_end))
if its==ite:ite+=1

t_slice=np.mean(C[its:ite,:],axis=0)
plt.close('all')
plt.figure(figsize=(18,6))

gs = gridspec.GridSpec(2,3,width_ratios=[2,1,1], height_ratios=[1 ,1])

ax=plt.subplot(gs[:,0])
plt.pcolormesh(t[0:-1],DX,C.T, cmap='inferno',vmax=10)
plt.colorbar()
plt.plot([t[its],t[its]],[0,20],':w',lw=0.5)
plt.plot([t[ite],t[ite]],[0,20],':w',lw=0.5)
plt.xlabel('Time [s]')
plt.ylabel('DX [m]')
ax.xaxis.set_major_locator(MultipleLocator(2))
ax.xaxis.set_minor_locator(MultipleLocator(0.25))
plt.ylim([0,20])

ax=plt.subplot(gs[0,1:])
plt.step(DX,t_slice)
plt.xlabel('DX [m]')
plt.ylabel('Counts')
plt.xlim([0,20])

ax=plt.subplot(gs[1,1],projection='polar')
plt.contourf(elev[its:ite],DX,C[its:ite,:].T,cmap='inferno')
ax.set_thetamin(-10)
ax.set_thetamax(90)
ax.set_rmax(20)

plt.xlabel('DX [m]')
plt.tight_layout()
plt.show()
