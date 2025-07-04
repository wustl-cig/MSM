"""
Based on: https://github.com/DPS2022/diffusion-posterior-sampling
"""
from abc import ABC, abstractmethod
import torch

__CONDITIONING_METHOD__ = {}

def register_conditioning_method(name: str):
    def wrapper(cls):
        if __CONDITIONING_METHOD__.get(name, None):
            raise NameError(f"Name {name} is already registered!")
        __CONDITIONING_METHOD__[name] = cls
        return cls
    return wrapper

def get_conditioning_method(name: str, operator, noiser, **kwargs):
    if __CONDITIONING_METHOD__.get(name, None) is None:
        raise NameError(f"Name {name} is not defined!")
    return __CONDITIONING_METHOD__[name](operator=operator, noiser=noiser, **kwargs)

    
class ConditioningMethod(ABC):
    def __init__(self, operator, noiser, **kwargs):
        self.operator = operator
        self.noiser = noiser
    
    def project(self, data, noisy_measurement, **kwargs):
        return self.operator.project(data=data, measurement=noisy_measurement, **kwargs)
    
    def grad_and_value(self, x_prev, x_0_hat, measurement, **kwargs):
        if self.noiser.__name__ == 'gaussian':
            difference = measurement - self.operator.forward(x_0_hat, **kwargs)
            norm = torch.linalg.norm(difference)
            norm_grad = torch.autograd.grad(outputs=norm, inputs=x_prev, allow_unused = False)[0]
        
        elif self.noiser.__name__ == 'poisson':
            Ax = self.operator.forward(x_0_hat, **kwargs)
            difference = measurement-Ax
            norm = torch.linalg.norm(difference) / measurement.abs()
            norm = norm.mean()
            norm_grad = torch.autograd.grad(outputs=norm, inputs=x_prev)[0]

        else:
            raise NotImplementedError
             
        return norm_grad, norm
   
    @abstractmethod
    def conditioning(self, x_t, measurement, noisy_measurement=None, **kwargs):
        pass
    
@register_conditioning_method(name='vanilla')
class Identity(ConditioningMethod):
    # just pass the input without conditioning
    def conditioning(self, x_t, **kwargs):
        return x_t
    
@register_conditioning_method(name='projection')
class Projection(ConditioningMethod):
    def conditioning(self, x_t, noisy_measurement, **kwargs):
        x_t = self.project(data=x_t, noisy_measurement=noisy_measurement)
        return x_t


@register_conditioning_method(name='mcg')
class ManifoldConstraintGradient(ConditioningMethod):
    def __init__(self, operator, noiser, **kwargs):
        super().__init__(operator, noiser)
        self.scale = kwargs.get('scale', 1.0)
        
    def conditioning(self, x_0_hat, measurement, at, **kwargs):
        norm_grad, norm = self.grad_and_value(x_prev=x_0_hat, x_0_hat=x_0_hat, measurement=measurement, **kwargs)
        x_0_hat = x_0_hat.detach()
        # x_0_hat -= norm_grad * self.scale / at.sqrt()
        x_0_hat -= norm_grad * self.scale / at.sqrt()
    
        return x_0_hat, norm


@register_conditioning_method(name='dps')
class PosteriorSampling(ConditioningMethod):
    def __init__(self, operator, noiser, **kwargs):
        super().__init__(operator, noiser)

    def conditioning(self, x_prev, x_t, x_0_hat, measurement, dps_scale, **kwargs):
        norm_grad, norm = self.grad_and_value(x_prev=x_prev, x_0_hat=x_0_hat, measurement=measurement, **kwargs)
        x_t = x_t - norm_grad * dps_scale
        return x_t, norm


@register_conditioning_method(name='ambient_diffusion')
class PosteriorSampling(ConditioningMethod):
    def __init__(self, operator, noiser, **kwargs):
        super().__init__(operator, noiser)

    def conditioning(self, x_prev, x_t, x_0_hat, measurement, dps_scale, **kwargs):
        norm_grad, norm = self.grad_and_value(x_prev=x_prev, x_0_hat=x_0_hat, measurement=measurement, **kwargs)

        x_t = x_t - norm_grad * dps_scale

        return x_t, norm    


# ---------------
# Note that below class does nothing, just to avoid error shooting
# ---------------
@register_conditioning_method(name='measurement_diffusion')
class PosteriorSampling(ConditioningMethod):
    def __init__(self, operator, noiser, **kwargs):
        super().__init__(operator, noiser)
        self.num_stochastics = kwargs.get('num_stochastics', 1.0)

    def conditioning(self, x_prev, x_t, x_0_hat, measurement, **kwargs):
        return     

@register_conditioning_method(name='dds')
class PosteriorSampling(ConditioningMethod):
    def __init__(self, operator, noiser, **kwargs):
        super().__init__(operator, noiser)
        self.scale = kwargs.get('scale', 1.0)

    def conditioning(self, x_prev, x_t, x_0_hat, measurement, **kwargs):
        return 
         
@register_conditioning_method(name='ddnm')
class DDNMPlusCore(ConditioningMethod):
    def __init__(self, operator, noiser, **kwargs):
        super().__init__(operator, noiser)
        self.operator = operator
        self.sigma_y = noiser.sigma
        self.kappa = kwargs.get('kappa')

    def conditioning(self, measurement, x_0_hat, sigma_t, **kwargs):
        #Eq 19
        if self.kappa is not None:
            lambda_t = self.kappa
            gamma_t = (sigma_t**2 - (lambda_t*self.sigma_y)**2)**0.5
        elif sigma_t >= self.sigma_y: 
            lambda_t = 1
            gamma_t = (sigma_t**2 - (lambda_t*self.sigma_y)**2)**0.5
        else:
            lambda_t = sigma_t/(self.sigma_y)
            gamma_t = 0
            
        #Eq 17
        x_0_hat = x_0_hat + lambda_t * (self.operator.pseudoinverse(measurement) - self.operator.pseudoinverse(self.operator.forward(x_0_hat)))
        x_0_hat = torch.clamp(x_0_hat, -1.0, 1.0)
        x_0_hat = x_0_hat.type(x_0_hat.dtype)
        
        return x_0_hat, gamma_t