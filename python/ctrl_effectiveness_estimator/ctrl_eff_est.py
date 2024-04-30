import numpy as np
import scipy as sp
from scipy import signal
import matplotlib.pyplot as plt

import sys
from os import path, getenv

class CtrlEffEst(object):
    def __init__(self, parsed_log, first_order_dyn_list, second_order_filter_cutoff, is_servo, actuator_indices, fs=500.):
        '''
        Initialization function of control effectiveness estimator
        '''
        self.data = parsed_log.get_message_dict('STAB_ATTITUDE')

        self.N_act = len(actuator_indices)
        self.actuator_indices = actuator_indices

        # Copy variables
        self.first_order_dyn_list = first_order_dyn_list # First order actuator dynamics constant
        self.second_order_filter_cutoff= second_order_filter_cutoff # Butterworth second order cutoff frequency [Hz]
        self.is_servo = is_servo # bool list giving if actuator is servo (aerodynamic surface control)
        self.fs = fs # Sample frequency [Hz]
        self.dt = 1./fs # Sample time [s]


    def get_effectiveness_values(self, plot = True):
        '''
        Returns effectiveness values for each actuator and axis 
        '''
        # Order data arrays
        t = np.array(self.data['t'])
        cmd = np.array(self.data['u']['data'])
        # Convert pwm to command

        rate_p = np.array(self.data['angular_rate_p']['data'])#/180.*np.pi # rad/s
        rate_q = np.array(self.data['angular_rate_q']['data'])#/180.*np.pi # rad/s
        rate_r = np.array(self.data['angular_rate_r']['data'])#/180.*np.pi # rad/s
        rates = np.array([rate_p,rate_q,rate_r]).T

        # Apply actuator dynamics to commands
        for i in range(self.N_act):
            zi = sp.signal.lfilter_zi([self.first_order_dyn_list[i]], [1, -(1-self.first_order_dyn_list[i])])
            #filtered_cmd = sp.signal.lfilter([self.first_order_dyn_list[i]], [1, -(1-self.first_order_dyn_list[i])], cmd[:,i], zi=zi*cmd[:,i][0])[0]
            filtered_cmd = sp.signal.lfilter([self.first_order_dyn_list[i]], [1, -(1-self.first_order_dyn_list[i])], cmd[:,i])
            if i == 0:
                cmd_a_T = np.array([filtered_cmd]) # Transpose of cmd_a
            else:
                cmd_a_T = np.vstack((cmd_a_T, filtered_cmd))

        cmd_a = cmd_a_T.T

        # 2nd order bUtterworth noise filter
        b, a = sp.signal.butter(2, self.second_order_filter_cutoff/(self.fs/2), 'low', analog=False)

        # Filter signals and cmds
        #zi = np.array([sp.signal.lfilter_zi(b, a)]).T
        rates_f = sp.signal.lfilter(b, a, rates, axis=0)
        cmd_af = sp.signal.lfilter(b, a, cmd_a, axis=0)

        # Apply finite difference methods to get anfular accelerarions
        d_rates = (np.vstack((np.zeros((1,3)), np.diff(rates_f,1,axis=0)))*self.fs)
        dd_rates = (np.vstack((np.zeros((1,3)), np.diff(d_rates,1,axis=0)))*self.fs)

        d_cmd= (np.vstack((np.zeros((1,self.N_act)), np.diff(cmd_af,1,axis=0)))*self.fs)
        dd_cmd = (np.vstack((np.zeros((1,self.N_act)), np.diff(d_cmd,1,axis=0)))*self.fs)

        t = t
        # Construct A matrix
        for i in range(len(self.actuator_indices)):
            row = d_cmd[:,i]
            
            if i == 0:
                A = row
            else:
                A = np.vstack((A, row))

        A = A.T

        # Remove first 2 seconds to align filters
        A = A[int(self.fs):-1]
        print(A)
        dd_rates_sliced = dd_rates[int(self.fs):-1]
    
        # Perform LMS to get effectiveness values per actuator per axis
        g1_lstsq = np.linalg.lstsq(A,dd_rates_sliced, rcond=0.5)
        g1_matrix = g1_lstsq[0]
        g1_residuals = g1_lstsq
        print('g1_matrix: ', g1_matrix)
        print('g1_residuals: ', g1_residuals)
        #g1_matrix = [[0.00072, 0.00095, 0.], [-0.00072, 0.00095, 0.], [0.00145, -0.001425, 0.], [-0.00145, -0.001425,0.]]

        # Yaw effectiveness
        A = np.vstack([d_cmd[:,4], rates_f[:,2]]).T
        print(d_cmd[:,4])
        print(rates_f[:,2])

        print(A)

        y = dd_rates[:,2]

        yaw_eff = np.linalg.lstsq(A, y, rcond=0.5)

        print("yaw_eff = " + str(yaw_eff))
        

        if plot:
            plt.figure('roll d_cmd*g1')
            for i in range(len(self.actuator_indices)):
                plt.subplot(self.N_act, 1, i+1)
                plt.plot(t, dd_rates.T[0])
                plt.plot(t, d_cmd.T[i] * g1_matrix[i][0])

            plt.figure('pitch d_cmd*g1')
            for i in range(len(self.actuator_indices)):
                plt.subplot(self.N_act, 1, i+1)
                plt.plot(t, dd_rates.T[1])
                plt.plot(t, d_cmd.T[i] * g1_matrix[i][1])

            plt.figure('yaw d_cmd*g1')
            for i in range(len(self.actuator_indices)):
                plt.subplot(self.N_act, 1, i+1)
                plt.plot(t, dd_rates.T[2])
                plt.plot(t, d_cmd.T[i] * g1_matrix[i][2])

            # Debug plots
            plt.figure('Test')
            plt.plot(t, d_cmd[:,1])
            plt.plot(t, d_cmd[:,3])
            plt.figure('TEST1')
            #plt.plot(t, cmd[:,0])
            plt.plot(t, cmd[:,1])
            #plt.plot(t, cmd[:,2])
            plt.plot(t, cmd[:,3])
            #plt.plot(t, cmd_af[:,0])
            #plt.plot(t, cmd_af[:,1])
            #plt.plot(t, cmd_af[:,2])
            #plt.plot(t, cmd_af[:,3])
            #plt.plot(t, cmd_af[:,0])
            #plt.figure('TEST2')
            #plt.plot(t, rates.T[0])
            #plt.plot(t, rates_f.T[0])
            #plt.plot(t, d_rates.T[0])
            #plt.plot(t, cmd_a.T[0])
            #plt.plot(t, cmd_af.T[0])
            plt.show()