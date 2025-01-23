function cycl_plot_actuators(ac_data, order)

    rpm1 = ac_data.SERIAL_ACT_T4_IN.motor_1_rpm;
    rpm2 = ac_data.SERIAL_ACT_T4_IN.motor_2_rpm;
    throttle_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.throttle/96;

    pos1 = double(ac_data.SERIAL_ACT_T4_IN.rotor_1_az_angle)/100;
    pos2 = double(ac_data.SERIAL_ACT_T4_IN.rotor_2_az_angle)/100;
    % cmd_servo1 = double(ac_data.SERIAL_ACT_T4_OUT.rotor_1_az_angle_cmd)/100;
    % cmd_servo2 = double(ac_data.SERIAL_ACT_T4_OUT.rotor_2_az_angle_cmd)/100;

    tiledlayout(4, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    ax1 = nexttile;
    hold on; zoom on;
    h1 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, pos1, 'LineWidth', 1.5);
    h2 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, pos2, 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('Flap deflection [deg]');
    title('Flap deflection');
    grid on;

    ax2 = nexttile;
    hold on; zoom on;
    h3 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, rpm1, 'LineWidth', 1.5);
    h4 = plot(ac_data.SERIAL_ACT_T4_IN.timestamp, rpm2, 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('Prop speed [rpm]');
    title('RPM');
    grid on;

    ax3 = nexttile;
    hold on; zoom on;
    h5 = plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, ac_data.SERIAL_ACT_T4_OUT.motor_1_dshot_cmd, 'LineWidth', 1.5);
    h6 = plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, ac_data.SERIAL_ACT_T4_OUT.motor_2_dshot_cmd, 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('DShot Command');
    title('DShot Command');
    grid on;

    ax4 = nexttile;
    hold on; zoom on;
    h7 = plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, throttle_percent, 'LineWidth', 1.5);
    xlabel('Time [s]');
    ylabel('RC Throttle [%]');
    title('Throttle');
    grid on;

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1, ax2});
    legend(ax1, [h1, h2], {'delta 1', 'delta 2'});
    legend(ax2, [h3, h4], {'RPM 1', 'RPM 2'});
    legend(ax3, [h5, h6], {'DShot Cmd 1', 'DShot Cmd 2'});
    legend(ax4, [h7], {'Throttle'});
    
    linkaxes([ax1,ax2,ax3,ax4],'x');

end