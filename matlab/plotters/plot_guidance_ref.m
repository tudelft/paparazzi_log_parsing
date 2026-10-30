function plot_guidance_ref(ac_data)
%PLOT_GUIDANCE_REF Horizontal guidance: setpoint, reference and estimate of the position and speed
%   (GUIDANCE_H_REF_INT, INS), and the height from the barometer against the estimate (INS_Z).
    has_ref = pprz.has(ac_data, 'GUIDANCE_H_REF_INT');
    has_ins = pprz.has(ac_data, 'INS');
    has_z = pprz.has(ac_data, 'INS_Z');
    axs = pprz.tiles(4 * has_ref + has_z, 'Guidance reference');
    k = 0;
    if has_ref
        r = ac_data.GUIDANCE_H_REF_INT;
        rows = {'x', 'North [m]', ''; 'y', 'East [m]', ''; 'x', 'North speed [m/s]', 'd'; 'y', 'East speed [m/s]', 'd'};
        for i = 1:4
            [c, d] = deal(rows{i, 1}, rows{i, 3});
            k = k + 1;
            plot(axs(k), r.t, r.(['sp_' c d]), ':', 'LineWidth', 1.2, 'DisplayName', 'Setpoint');
            plot(axs(k), r.t, r.(['ref_' c d]), '--', 'DisplayName', 'Reference');
            if has_ins
                plot(axs(k), ac_data.INS.t, ac_data.INS.(['ins_' c d]), 'DisplayName', 'Estimate');
            end
            ylabel(axs(k), rows{i, 2});
        end
        legend(axs(1), 'Location', 'best');
    end
    if has_z
        z = ac_data.INS_Z;
        k = k + 1;
        plot(axs(k), z.t, -z.ins_z, 'DisplayName', 'Estimate');
        plot(axs(k), z.t, -z.baro_z, 'DisplayName', 'Barometer');
        ylabel(axs(k), 'Height [m]');
        legend(axs(k), 'Location', 'best');
    end
end
