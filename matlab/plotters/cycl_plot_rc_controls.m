function cycl_plot_rc_controls(ac_data, order)

    tiledlayout(3, 1, 'Padding', 'compact', 'TileSpacing', 'compact');
    
    throttle_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.throttle/96;
    kill_percent = 100 - (ac_data.ROTORCRAFT_RADIO_CONTROL.kill + 9600)/(2*96);

    blue_sw = double(string(ac_data.PPM.values(:,6)));
    green_sw = double(string(ac_data.PPM.values(:,7)));
    yellow_sw = double(string(ac_data.PPM.values(:,8)));

    ax1 = nexttile;
    hold on; grid on; zoom on;
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, throttle_percent, LineWidth=1.5);
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, kill_percent, 'Color', 'r', LineWidth=1.5);
    legend('throttle [%]', 'kill [%]')
    xlabel('time [s]');
    ylabel('');
    title('throttle and kill');
    hold off;

    ax2 = nexttile;
    hold on; grid on; zoom on;
    plot(ac_data.PPM.timestamp, blue_sw, 'Color', 'b', LineWidth=1.5);
    plot(ac_data.PPM.timestamp, green_sw, 'Color', 'g', LineWidth=1.5);
    plot(ac_data.PPM.timestamp, yellow_sw, 'Color', [0.8, 0.8, 0], LineWidth=1.5);
    legend('blue switch [ppm]', 'green switch [ppm]', 'yellow switch [ppm]');
    xlabel('time [s]');
    ylabel('');
    title('RC switches');
    hold off;

    ax3 = nexttile;
    hold on; grid on; zoom on;
    plot(ac_data.PPM.timestamp, ac_data.PPM.ppm_rate, 'Color', 'r', LineWidth=1.5);
    plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, ac_data.ROTORCRAFT_RADIO_CONTROL.status*100, 'Color', 'b', LineWidth=1.5);
    legend('PPM rate', 'RC status [0-100-200(very lost)]');
    xlabel('time [s]');
    ylabel('');
    title('PPM rate and RC status');
    hold off;

    linkaxes([ax1,ax2,ax3],'x')

end