function plot_hybrid_guidance(ac_data, vert)
    if ~isfield(ac_data, 'HYBRID_GUIDANCE')
        return
    end
    
    % Plot the hybrid guidance
    ax1 = subplot(3,1,1);
    plot(ac_data.HYBRID_GUIDANCE.timestamp, [ac_data.HYBRID_GUIDANCE.norm_ref_speed_alt, ac_data.HYBRID_GUIDANCE.speed_sp_x_alt, ac_data.HYBRID_GUIDANCE.speed_sp_y_alt]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Target speeds');
    xlabel('Time [s]');
    legend('Norm ref speed [m/s]', 'Speed x sp [m/s]', 'Speed y sp [m/s]');

    ax2 = subplot(3,1,2);
    plot(ac_data.HYBRID_GUIDANCE.timestamp, [ac_data.HYBRID_GUIDANCE.heading_diff, ac_data.HYBRID_GUIDANCE.pos_err_x_alt, ac_data.HYBRID_GUIDANCE.pos_err_y_alt]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Errors');
    xlabel('Time [s]');
    legend('Heading err [deg]', 'X-pos err [m]', 'Y-pos err [m]');

    ax3 = subplot(3,1,3);
    plot(ac_data.HYBRID_GUIDANCE.timestamp, [ac_data.HYBRID_GUIDANCE.wind_x_alt, ac_data.HYBRID_GUIDANCE.wind_y_alt]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Wind');
    xlabel('Time [s]');
    legend('x [m/s]', 'y [m/s]');

    sgtitle('Hyrbid guidance')
    linkaxes([ax1,ax2,ax3],'x')
end