function plot_hover_loop(ac_data, vert)
    if ~isfield(ac_data, 'HOVER_LOOP')
        return
    end

    % Plot the hover loop
    ax1 = subplot(2,1,1);
    x_err = ac_data.HOVER_LOOP.est_x_alt-ac_data.HOVER_LOOP.sp_x_alt;
    y_err = ac_data.HOVER_LOOP.est_y_alt-ac_data.HOVER_LOOP.sp_y_alt;
    plot(ac_data.HOVER_LOOP.timestamp, [x_err, y_err]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Errors');
    xlabel('Time [s]');
    legend('X-err', 'Y-err');
    
    ax2 = subplot(2,1,2);
    if isfield(ac_data, 'ROTORCRAFT_FP')
        plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.thrust]);
        if exist('vert','var')
            hold on;
            plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
        end
        title('Thurst');
        xlabel('Time [s]');
        legend('Thrust');
    end
    
    sgtitle('Hover loop')
    linkaxes([ax1,ax2],'x')
end