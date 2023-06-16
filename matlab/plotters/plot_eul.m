function plot_eul_zxy(ac_data, order)
    if ~isfield(ac_data, 'AHRS_REF_QUAT')
        return
    end

    if ~exist('order','var')
        order = 'ZYX';
    end
    
    % Plot the Euler angles
    msg = ac_data.AHRS_REF_QUAT;

    quat = double([msg.body_qi msg.body_qx msg.body_qy msg.body_qz]);
    refquat = double([msg.ref_qi msg.ref_qx msg.ref_qy msg.ref_qz]);
    [refquat_t,irefquat_t,~] = unique(msg.timestamp);
    quat = quat(irefquat_t,:);
    refquat = refquat(irefquat_t,:);

    if strcmp(order,'ZXY')
        [psi, phi, theta] = quat2angle(quat,order);
        [refpsi, refphi, reftheta] = quat2angle(refquat,order);
    elseif strcmp(order,'ZYX')
        [psi, theta, phi] = quat2angle(quat,order);
        [refpsi, reftheta, refphi] = quat2angle(refquat,order);
    else
        disp('Rotation order not available')
    end

    figure;
    ax1 = subplot(3,1,1);
    plot(refquat_t,rad2deg(theta),refquat_t,rad2deg(reftheta));
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('theta'); xlabel('time(s)'); ylabel('theta (deg)'); legend('theta','ref')

    ax2 = subplot(3,1,2);
    plot(refquat_t,rad2deg(phi),refquat_t,rad2deg(refphi));
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title("phi");xlabel('time(s)');ylabel('phi (deg)'); legend('phi','ref')

    ax3 = subplot(3,1,3);
    plot(refquat_t,rad2deg(psi),refquat_t,rad2deg(refpsi));
    title('psi');xlabel('time(s)');ylabel('psi (deg)'); legend('psi','ref')

    sgtitle(['Euler ', order, ' order'])
    linkaxes([ax1,ax2,ax3],'x')
end