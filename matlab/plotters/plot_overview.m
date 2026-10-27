function plot_overview(ac_data, f)
%PLOT_OVERVIEW One page per flight: height, speeds, attitude, battery and autopilot mode.
    rows = {};
    if pprz.has(ac_data, 'ROTORCRAFT_FP'), rows = [rows, {'height', 'speed', 'attitude'}]; end
    if pprz.has(ac_data, 'ENERGY'), rows{end + 1} = 'battery'; end
    if pprz.has(ac_data, 'ROTORCRAFT_STATUS'), rows{end + 1} = 'mode'; end
    if isempty(rows)
        return
    end
    tiledlayout(numel(rows), 1, 'Padding', 'compact', 'TileSpacing', 'compact');
    axs = gobjects(1, numel(rows));
    for i = 1:numel(rows)
        axs(i) = nexttile;
        hold on; grid on;
        switch rows{i}
            case 'height'
                fp = ac_data.ROTORCRAFT_FP;
                plot(fp.timestamp, fp.up, fp.timestamp, fp.carrot_up, '--');
                ylabel('Up [m]'); legend('Up', 'Target');
            case 'speed'
                fp = ac_data.ROTORCRAFT_FP;
                plot(fp.timestamp, hypot(fp.veast, fp.vnorth), fp.timestamp, fp.vup);
                names = {'Ground speed', 'Vertical speed'};
                if pprz.has(ac_data, 'AIR_DATA')
                    plot(ac_data.AIR_DATA.timestamp, ac_data.AIR_DATA.airspeed);
                    names{end + 1} = 'Airspeed'; %#ok<AGROW>
                end
                ylabel('[m/s]'); legend(names);
            case 'attitude'
                fp = ac_data.ROTORCRAFT_FP;
                plot(fp.timestamp, [fp.phi, fp.theta]);
                ylabel('[deg]'); legend('Phi', 'Theta');
            case 'battery'
                e = ac_data.ENERGY;
                yyaxis left; plot(e.timestamp, e.voltage); ylabel('Voltage [V]');
                yyaxis right; plot(e.timestamp, e.current); ylabel('Current [A]');
            case 'mode'
                m = ac_data.ROTORCRAFT_STATUS;
                stairs(m.timestamp, double(m.ap_mode), 'LineWidth', 1.2);
                modes = unique(double(m.ap_mode));
                if nargin > 1 && ~isempty(modes)
                    yticks(modes); yticklabels(pprz.enum(f, 'ROTORCRAFT_STATUS', 'ap_mode', modes));
                end
                ylabel('AP mode');
        end
    end
    xlabel(axs(end), 'Time [s]');
    linkaxes(axs, 'x');
    if nargin > 1
        sgtitle(strrep(f.title, '_', '\_'));
    end
end
