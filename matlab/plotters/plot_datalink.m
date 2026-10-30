function plot_datalink(ac_data)
%PLOT_DATALINK Telemetry link (DATALINK_REPORT): time since the last uplink message and the link rates.
    m = ac_data.DATALINK_REPORT;
    axs = pprz.tiles(2, 'Datalink');
    plot(axs(1), m.t, m.uplink_lost_time, 'DisplayName', 'Uplink lost time');
    ylabel(axs(1), 'Uplink lost [s]');
    plot(axs(2), m.t, double(m.downlink_rate) / 1000, 'DisplayName', 'Downlink [kB/s]');
    plot(axs(2), m.t, double(m.uplink_rate), 'DisplayName', 'Uplink [msgs/s]');
    ylabel(axs(2), 'Rate');
    legend(axs(2), 'Location', 'best');
end
