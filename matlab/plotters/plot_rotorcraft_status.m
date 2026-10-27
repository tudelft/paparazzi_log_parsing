function plot_rotorcraft_status(ac_data, f)
%PLOT_ROTORCRAFT_STATUS In flight / motors on, autopilot mode (named when f is given) and arming status.
    if ~pprz.has(ac_data, 'ROTORCRAFT_STATUS')
        return
    end
    m = ac_data.ROTORCRAFT_STATUS;
    ax1 = subplot(3, 1, 1);
    area(m.t, double([m.ap_in_flight, m.ap_motors_on]), 'FaceAlpha', 0.4);
    title('In flight / motors on');
    legend('In flight', 'Motors on');
    ax2 = subplot(3, 1, 2);
    stairs(m.t, double(m.ap_mode), 'LineWidth', 1.2);
    title('Autopilot mode');
    modes = unique(double(m.ap_mode));
    if nargin > 1 && ~isempty(modes)
        yticks(modes);
        yticklabels(pprz.enum(f, 'ROTORCRAFT_STATUS', 'ap_mode', modes));
    end
    ax3 = subplot(3, 1, 3);
    stairs(m.t, double(m.arming_status), 'LineWidth', 1.2);
    title('Arming status');
    xlabel('Time [s]');
    sgtitle('Status');
    linkaxes([ax1, ax2, ax3], 'x');
end
