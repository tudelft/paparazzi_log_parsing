function cycl_plot_airspeed(ac_data, order)

    ax = axes;

    hold on; grid on; zoom on;
    h1 = plot(ac_data.AIR_DATA.timestamp, ac_data.AIR_DATA.airspeed);
    xlabel('Time [s]');
    ylabel('Airspeed [m/s]');
    title('Airspeed');

    % background color fill for various flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_fill_mode_regions(mode_values, mode_timestamps, {ax});
    legend(ax, h1, 'Airspeed');
    
end