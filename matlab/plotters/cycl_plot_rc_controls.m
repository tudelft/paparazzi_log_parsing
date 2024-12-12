function cycl_plot_rc_controls(ac_data, order)

    tiledlayout(3, 1, 'Padding', 'compact', 'TileSpacing', 'compact');
    
    throttle_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.throttle/96;
    pitch_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.pitch/96;
    roll_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.roll/96;
    yaw_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.yaw/96;
    kill_percent = (ac_data.ROTORCRAFT_RADIO_CONTROL.kill + 9600)/(2*96);

    blue_sw = double(string(ac_data.PPM.values(:,5)));
    green_sw = double(string(ac_data.PPM.values(:,7)));

    ax1 = nexttile;
    hold on; grid on; zoom on;
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, throttle_percent, LineWidth=1.5);
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, kill_percent, 'Color', 'r', LineWidth=1);
    legend('throttle [%]', 'kill [%]')
    xlabel('time [s]');
    ylabel('');
    title('throttle and kill');
    hold off;
    
    ax2 = nexttile;
    hold on; grid on; zoom on;
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, pitch_percent, LineWidth=1);
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, roll_percent, LineWidth=1);
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, yaw_percent, LineWidth=1);
    legend('pitch [%]', 'roll [%]', 'yaw [%]');
    xlabel('time [s]');
    ylabel('');
    title('RC sticks');
    hold off;

    ax3 = nexttile;
    hold on; grid on; zoom on;
    plot(ac_data.PPM.timestamp, blue_sw, 'Color', 'b', LineWidth=1.5);
    plot(ac_data.PPM.timestamp, green_sw, 'Color', 'g', LineWidth=1.5);
    plot(ac_data.PPM.timestamp, ac_data.PPM.ppm_rate*10, 'Color', 'r', LineWidth=1);
    legend('blue switch [ppm]', 'green switch [ppm]', 'ppm rate x 10');
    xlabel('time [s]');
    ylabel('');
    title('RC switches and ppm rate');
    hold off;

    linkaxes([ax1,ax2,ax3],'x')

end