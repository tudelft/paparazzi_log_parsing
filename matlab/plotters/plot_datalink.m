function plot_datalink(ac_data)
%PLOT_DATALINK Telemetry link (DATALINK_REPORT): time since the last uplink message, the link rates and, in logs
%   from pprzlink 2026-11 on, the uplink frames rejected (uplink_err) and dropped (uplink_ovrn) per report.
    m = ac_data.DATALINK_REPORT;
    errors = isfield(m, 'uplink_err') || isfield(m, 'uplink_ovrn');
    axs = pprz.tiles(2 + errors, 'Datalink');
    plot(axs(1), m.t, m.uplink_lost_time, 'DisplayName', 'Uplink lost time');
    ylabel(axs(1), 'Uplink lost [s]');
    plot(axs(2), m.t, double(m.downlink_rate) / 1000, 'DisplayName', 'Downlink [kB/s]');
    plot(axs(2), m.t, double(m.uplink_rate), 'DisplayName', 'Uplink [msgs/s]');
    ylabel(axs(2), 'Rate');
    legend(axs(2), 'Location', 'best');
    if errors
        fields = {'uplink_err', 'Rejected (checksum, length)'; 'uplink_ovrn', 'Dropped (overrun)'};
        for k = 1:size(fields, 1)
            if isfield(m, fields{k, 1})
                plot(axs(3), m.t, pprz.counter_steps(m.(fields{k, 1}), 256), 'DisplayName', fields{k, 2});
            end
        end
        ylabel(axs(3), 'Uplink frames per report');
        legend(axs(3), 'Location', 'best');
    end
end
