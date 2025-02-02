#!/usr/bin/env python
# coding: utf-8

# ### Import modules and verify they work? 

# In[1]:


# general python
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
import numpy as np
import os
from pathlib import Path
import yaml
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import scipy
import xarray as xr
from tqdm import tqdm
import glob
from devtools import pprint
from tqdm import tqdm
import ewatercycle
import ewatercycle.forcing
import ewatercycle.models
from ewatercycle.forcing import sources
from ewatercycle.models import HBVLocal
# from ewatercycle_DA.local_models.HBV import HBVLocal
from ewatercycle_DA import DA
import gc

path = Path.cwd()
forcing_path = path / "Forcing"
observations_path = path / "Observations"
output_path = path / "Output"
paths = forcing_path, output_path, observations_path
figure_path = path / "Figures"
for path_i in list(paths) + [figure_path]:
    path_i.mkdir(exist_ok=True)


experiment_start_date = "1997-08-01T00:00:00Z"
experiment_end_date = "2000-09-01T00:00:00Z"

HRU_id_int = 14138900
HRU_id = f'{HRU_id_int}'
if len(HRU_id) < 8: 
    HRU_id = '0' + HRU_id
    
alpha = 1.26
assimilate_window = 3 # after how many time steps to run the assimilate steps
n_particles = 200

model_name = "HBVLocal"

save = True

camels_forcing = sources.HBVForcing(start_time = experiment_start_date,
                          end_time = experiment_end_date,
                          directory = forcing_path,
                          camels_file = f'{HRU_id}_lump_cida_forcing_leap.txt',
                          alpha = alpha,
                          )


# In[8]:


def H(Z):
    """Operator function extracts observable state from the state vector"""
    len_Z = 15
    if len(Z) == len_Z:
        return Z[-1] 
    else: 
        raise SyntaxWarning(f"Length of statevector should be {len_Z} but is {len(Z)}")
        

def calc_NSE(Qo, Qm):
    QoAv  = np.mean(Qo)
    ErrUp = np.sum((Qm - Qo)**2)
    ErrDo = np.sum((Qo - QoAv)**2)
    return 1 - (ErrUp / ErrDo)
    
def calc_log_NSE(Qo, Qm):
    QoAv  = np.mean(Qo)
    ErrUp = np.sum((np.log(Qm) - np.log(Qo))**2)
    ErrDo = np.sum((np.log(Qo) - np.log(QoAv))**2)
    return 1 - (ErrUp / ErrDo)



# In[9]:


## Array of initial storage terms - we keep these constant for now 
##              Si,  Su, Sf, Ss
s_0 = np.array([0,  100,  0,  5, 0])

## Array of parameters min/max bounds as a reference
##                      Imax,  Ce,  Sumax, beta,  Pmax,  T_lag,   Kf,   Ks, FM
p_min_initial= np.array([0,   0.2,  40,    .5,   .001,   1,     .01,  .0001, 6])
p_max_initial = np.array([8,    1,  800,   4,    .3,     10,    .1,   .01, 0.1])
p_names = ["$I_{max}$",  "$C_e$",  "$Su_{max}$", "β",  "$P_{max}$",  "$T_{lag}$",   "$K_f$",   "$K_s$", "FM"]
S_names = ["Interception storage", "Unsaturated Rootzone Storage", "Fastflow storage", "Groundwater storage", "Snowpack storage"]
param_names = ["Imax","Ce",  "Sumax", "Beta",  "Pmax",  "Tlag",   "Kf",   "Ks", "FM"]
stor_names = ["Si", "Su", "Sf", "Ss", "Sp"]


# In[10]:


def experiment_run(n_particles, p_max_initial, p_min_initial, s_0, model_name, camels_forcing, 
                   paths, HRU_id, sigma_tuple, H, assimilate_window, save):

    forcing_path, output_path, observations_path = paths
    # set up ensemble
    ensemble = DA.Ensemble(N=n_particles)
    ensemble.setup()
    
    # initial values
    array_random_num = np.array([[np.random.random() for i in range(len(p_max_initial))] for i in range(n_particles)])
    p_intial = p_min_initial + array_random_num * (p_max_initial-p_min_initial)
    
    # values wihch you 
    setup_kwargs_lst = []
    for index in range(n_particles):
        setup_kwargs_lst.append({'parameters':','.join([str(p) for p in p_intial[index]]), 
                                'initial_storage':','.join([str(s) for s in s_0]),
                                 })
    
    
    # not required as of ewatercycle-HBV==1.8.2
    # ensemble.loaded_models.update({'HBVLocal': HBVLocal})
    
    # this initializes the models for all ensemble members. 
    ensemble.initialize(model_name=[model_name]*n_particles,
                        forcing=[camels_forcing]*n_particles,
                        setup_kwargs=setup_kwargs_lst) 
    
    # create a reference model
    ref_model = ensemble.ensemble_list[0].model
    ds = xr.open_dataset(forcing_path / ref_model.forcing.pr)
    basin_area = ds.attrs['area basin(m^2)']
    ds.close()

    
    # load observations
    observations = observations_path / f'{HRU_id}_streamflow_qc.txt'
    cubic_ft_to_cubic_m = 0.0283168466 
    
    new_header = ['GAGEID','Year','Month', 'Day', 'Streamflow(cubic feet per second)','QC_flag']
    new_header_dict = dict(list(zip(range(len(new_header)),new_header)))
    df_Q = pd.read_fwf(observations,delimiter=' ',encoding='utf-8',header=None)
    df_Q = df_Q.rename(columns=new_header_dict)
    df_Q['Streamflow(cubic feet per second)'] = df_Q['Streamflow(cubic feet per second)'].apply(lambda x: np.nan if x==-999.00 else x)
    df_Q['Q (m3/s)'] = df_Q['Streamflow(cubic feet per second)'] * cubic_ft_to_cubic_m
    df_Q['Q'] = df_Q['Q (m3/s)'] / basin_area * 3600 * 24 * 1000 # m3/s -> m/s ->m/d -> mm/d
    df_Q.index = df_Q.apply(lambda x: pd.Timestamp(f'{int(x.Year)}-{int(x.Month)}-{int(x.Day)}'),axis=1)
    df_Q.index.name = "time"
    df_Q.drop(columns=['Year','Month', 'Day','Streamflow(cubic feet per second)'],inplace=True)
    df_Q = df_Q.dropna(axis=0)
    
    ds_obs_dir = observations_path / f'{HRU_id}_streamflow_qc.nc'
    ds_obs = xr.Dataset(data_vars=df_Q[['Q']])
    if not ds_obs_dir.exists():
        ds_obs.to_netcdf(ds_obs_dir)
    
    # set up hyperparameters
    sigma_pp , sigma_ps, sigma_w = sigma_tuple
    lst_like_sigma = [sigma_pp] * 9 + [sigma_ps] * 5 + [0]
    hyper_parameters = {'like_sigma_weights' : sigma_w,
                        'like_sigma_state_vector' : lst_like_sigma,
                       }
    
    ensemble.initialize_da_method(ensemble_method_name = "PF", 
                                  hyper_parameters=hyper_parameters,                           
                                  state_vector_variables = "all", # the next three are keyword arguments but are needed. 
                                  observation_path = ds_obs_dir,
                                  observed_variable_name = "Q",
                                  measurement_operator = H, 
                               
                                )
    # extract units for later
    state_vector_variables = ensemble.ensemble_list[0].variable_names
    units = {}
    for var in state_vector_variables:
        units.update({var : ref_model.bmi.get_var_units(var)})
        
    ## run!
    n_timesteps = int((ref_model.end_time - ref_model.start_time) /  ref_model.time_step)
    time = []
    lst_Q = [] 
    for i in tqdm(range(n_timesteps)):    
        time.append(pd.Timestamp(ref_model.time_as_datetime.date()))
        # update every 3 steps 
        if i % assimilate_window == 0: 
            assimilate = True 
        else:
            assimilate = False
        ensemble.update(assimilate=assimilate)
         
        #lst_state_vector.append(ensemble.get_state_vector())
        lst_Q.append(ensemble.get_value("Q").flatten())
    
    ensemble.finalize()
    
    # post process
    Q_m_arr = np.array(lst_Q).T
 
    del lst_Q
    
 
    df_ensemble = pd.DataFrame(data=Q_m_arr[:,:len(time)].T,index=time,columns=[f'particle {n}' for n in range(n_particles)])
    del Q_m_arr

    mean_ensemble = df_ensemble.T.mean()
    max_ensemble = df_ensemble.T.max()
    min_ensemble = df_ensemble.T.min()
    df_summary = pd.concat([mean_ensemble,max_ensemble,min_ensemble, ds_obs['Q'].sel(time=time).to_pandas()],axis=1)
    df_summary = df_summary.rename(columns={0:"mean",1:"max",2:"min",3:"obs"})

    gc.collect()
    return df_summary, time


# In[ ]:

def run(sigma_tuple):
    sigma_pp, sigma_ps, sigma_w = sigma_tuple
    df_summary, time = experiment_run(n_particles, p_max_initial, p_min_initial, s_0, model_name, camels_forcing,
                                                paths, HRU_id, sigma_tuple, H, assimilate_window , save)


    
    current_time = str(datetime.now())[:-10].replace(":","_")
    if save:
        df_summary.to_feather(output_path /f'{HRU_id}_pp-{sigma_pp}_sigma_ps-{sigma_ps}_w-{sigma_w}_N-{n_particles}_{current_time}.feather')
        
    NSE_mean = calc_NSE(df_summary['obs'].values, df_summary['mean'].values)
    NSE_mean_log = calc_log_NSE(df_summary['obs'].values, df_summary['mean'].values)
    
    np.savetxt(output_path /f'{HRU_id}_pp-{sigma_pp}_sigma_ps-{sigma_ps}_w-{sigma_w}_N-{n_particles}_{current_time}.txt', np.array([NSE_mean, NSE_mean_log]))
    
   
def main():

    for sigma_pp in [0.01,0.015, 0.02]:
        for sigma_ps in [1, 2, 2.5]:
            sigma_w = 0.5   # weights for resampling
            sigma_tuple = sigma_pp, sigma_ps, sigma_w 
            run(sigma_tuple)

   
if __name__ == "__main__":
    
    main()
   
