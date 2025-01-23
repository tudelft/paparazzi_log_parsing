function cycl_plot_rc_controls(ac_data, order)

    tiledlayout(3, 1, 'Padding', 'compact', 'TileSpacing', 'compact');
    
    throttle_percent = ac_data.ROTORCRAFT_RADIO_CONTROL.throttle/96;
    kill_percent = 100 - (ac_data.ROTORCRAFT_RADIO_CONTROL.kill + 9600)/(2*96);

    blue_sw = double(string(ac_data.PPM.values(:,6)));
    green_sw = double(string(ac_data.PPM.values(:,7)));
    yellow_sw = double(string(ac_data.PPM.values(:,8)));

    ax1 = nexttile;
    hold on; grid on; zoom on;
    h1 = plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, throttle_percent, LineWidth=1.5);
    h2 = plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, kill_percent, 'Color', 'r', LineWidth=1.5);
    xlabel('time [s]');
    ylabel('throttle [%] and kill');
    title('throttle and kill');
    hold off;

    ax2 = nexttile;
    hold on; grid on; zoom on;
    h3 = plot(ac_data.PPM.timestamp, blue_sw, 'Color', 'b', LineWidth=1.5);
    h4 = plot(ac_data.PPM.timestamp, green_sw, 'Color', 'g', LineWidth=1.5);
    h5 = plot(ac_data.PPM.timestamp, yellow_sw, 'Color', [0.8, 0.8, 0], LineWidth=1.5);
    xlabel('time [s]');
    ylabel('RC switches [ppm]');
    title('RC switches');
    hold off;

    ax3 = nexttile;
    hold on; grid on; zoom on;
    h6 = plot(ac_data.PPM.timestamp, ac_data.PPM.ppm_rate, 'Color', 'r', LineWidth=1.5);
    h7 = plot(ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp, ac_data.ROTORCRAFT_RADIO_CONTROL.status*100, 'Color', 'b', LineWidth=1.5);
    xlabel('time [s]');
    ylabel('PPM rate and RC status');
    title('PPM rate and RC status');
    hold off;

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1,ax2,ax3});
    legend(ax1, [h1,h2], {'throttle', '100% - kill'});
    legend(ax2, [h3,h4,h5], {'blue switch', 'green switch', 'yellow switch'});
    legend(ax3, [h6,h7], {'PPM rate', 'RC status [0-100(lost)-200(really lost)]'});

    linkaxes([ax1,ax2,ax3],'x');

end