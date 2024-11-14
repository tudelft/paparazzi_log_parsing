function plot_defl(ac_data, order)

    pos1 = double(ac_data.SERIAL_ACT_T4_IN.rotor_1_az_angle)/100;
    pos2 = double(ac_data.SERIAL_ACT_T4_IN.rotor_2_az_angle)/100;
    cmd_servo1 = double(ac_data.SERIAL_ACT_T4_OUT.rotor_1_az_angle_cmd)/100;
    cmd_servo2 = double(ac_data.SERIAL_ACT_T4_OUT.rotor_2_az_angle_cmd)/100;

    hold on; zoom on;
    plot(ac_data.SERIAL_ACT_T4_IN.timestamp, pos1, 'Color', 'm', 'LineWidth', 1.2);
    plot(ac_data.SERIAL_ACT_T4_IN.timestamp, pos2, 'Color', 'r', 'LineWidth', 1.2);
    plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, cmd_servo1, ':', 'Color', 'b');
    plot(ac_data.SERIAL_ACT_T4_OUT.timestamp, cmd_servo2, ':', 'Color', 'b');
    legend('pos1', 'pos2', 'cmd1', 'cmd2');
    xlabel('time(s)');
    ylabel('deg');
    title('deg');
    hold off;

end