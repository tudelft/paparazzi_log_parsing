function plot_rotorcraft_status(ac_data, f)
%PLOT_ROTORCRAFT_STATUS Autopilot mode and arming state (named when the export has the enums), in-flight and
%   motors-on flags, and the board voltage and CPU frame rate.
    m = ac_data.ROTORCRAFT_STATUS;
    axs = pprz.tiles(4, 'Status');
    named_stairs(axs(1), m.t, m.ap_mode, f, 'ap_mode');
    ylabel(axs(1), 'Autopilot mode');
    named_stairs(axs(2), m.t, m.arming_status, f, 'arming_status');
    ylabel(axs(2), 'Arming');
    stairs(axs(3), m.t, double(m.ap_motors_on) + 0.04, 'LineWidth', 1.2, 'DisplayName', 'Motors on');
    stairs(axs(3), m.t, double(m.ap_in_flight), 'LineWidth', 1.2, 'DisplayName', 'In flight');
    set(axs(3), 'YLim', [-0.2 1.2], 'YTick', [0 1], 'YTickLabel', {'no', 'yes'});
    legend(axs(3), 'Location', 'best');
    if isfield(m, 'vsupply')
        plot(axs(4), m.t, m.vsupply, 'DisplayName', 'Supply voltage [V]');
    end
    if isfield(m, 'frame_rate')
        plot(axs(4), m.t, double(m.frame_rate) / 100, 'DisplayName', 'Frame rate [100 Hz]');
    end
    legend(axs(4), 'Location', 'best');
    ylabel(axs(4), 'Board');
end

function named_stairs(ax, t, v, f, field)
    v = double(v);
    stairs(ax, t, v, 'LineWidth', 1.2, 'DisplayName', field);
    values = unique(v);
    if nargin > 3 && ~isempty(values)
        set(ax, 'YTick', values, 'YTickLabel', cellstr(pprz.enum(f, 'ROTORCRAFT_STATUS', field, values)), ...
            'YLim', [min(values) - 0.5, max(values) + 0.5]);
    end
end
