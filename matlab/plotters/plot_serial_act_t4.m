function plot_serial_act_t4(ac_data)
%PLOT_SERIAL_ACT_T4 Serial actuators (T4): rotor tilt angle and rpm (SERIAL_ACT_T4_IN), and the DShot
%   commands (SERIAL_ACT_T4_OUT) when logged.
    in = ac_data.SERIAL_ACT_T4_IN;
    out = pprz.has(ac_data, 'SERIAL_ACT_T4_OUT');
    axs = pprz.tiles(2 + out, 'Serial actuators (T4)');
    pprz.series(axs(1), in.t, double([in.rotor_1_az_angle, in.rotor_2_az_angle]) / 100, {'Rotor 1', 'Rotor 2'});
    ylabel(axs(1), 'Tilt angle [deg]');
    pprz.series(axs(2), in.t, [in.motor_1_rpm, in.motor_2_rpm], {'Motor 1', 'Motor 2'});
    ylabel(axs(2), 'Speed [rpm]');
    if out
        o = ac_data.SERIAL_ACT_T4_OUT;
        pprz.series(axs(3), o.t, [o.motor_1_dshot_cmd, o.motor_2_dshot_cmd], {'Motor 1', 'Motor 2'});
        ylabel(axs(3), 'DShot command');
    end
end
